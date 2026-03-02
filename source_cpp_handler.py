"""
source_cpp_handler.py
쿠팡플레이 핸들러 - Playwright 헤드리스 브라우저 기반 로그인

[로그인 방식]
- requests 단독 사용 시 Akamai Bot Manager 에 의해 403 차단됨
- Playwright(Chromium) 로 실제 브라우저처럼 로그인 후 쿠키 추출
- 추출한 쿠키를 requests.Session 에 주입하여 API 호출
"""

import json, os, re, time, traceback, uuid
import requests
from flask import Response, redirect

try:
    from .setup import P as _PLUGIN
except Exception:
    try:
        from setup import P as _PLUGIN
    except Exception:
        _PLUGIN = None


# ═══════════════════════════════════════════════════════
#  상수
# ═══════════════════════════════════════════════════════

CPP_HOST   = "https://www.coupangplay.com"
LOGIN_HOST = "https://login.coupang.com"
LOGIN_PAGE = f"{LOGIN_HOST}/login/login.pang"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BASE_HDR = {
    "User-Agent": UA,
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


# ═══════════════════════════════════════════════════════
#  헬퍼
# ═══════════════════════════════════════════════════════

def _log():
    if _PLUGIN: return _PLUGIN.logger
    import logging
    logging.basicConfig(level=logging.DEBUG)
    return logging.getLogger("cpp")

def _cfg(k, d=""):
    if _PLUGIN:
        v = _PLUGIN.ModelSetting.get(k)
        return v if v is not None else d
    return d

_g_sess = None

def _sess():
    global _g_sess
    if _g_sess is None: _g_sess = _new_sess()
    return _g_sess

def _new_sess():
    global _g_sess
    s = requests.Session()
    s.headers.update(BASE_HDR)
    _g_sess = s
    return s


# ═══════════════════════════════════════════════════════
#  로그인 (Playwright 헤드리스 브라우저)
#  requests 단독으로는 Akamai Bot Manager 에 의해 403 차단됨
# ═══════════════════════════════════════════════════════

def _do_login(username, password):
    log = _log()
    s = _new_sess()

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("[CPP] playwright 미설치. pip install playwright && playwright install chromium")
        return False

    log.info("[CPP] Playwright 브라우저로 로그인 시도")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        try:
            # 1) 쿠팡플레이 메인 -> 초기 쿠키/세션
            log.debug("[CPP] 쿠팡플레이 메인 접속")
            page.goto(CPP_HOST + "/", wait_until="domcontentloaded", timeout=20000)

            # 2) 로그인 페이지로 이동
            login_url = f"{LOGIN_PAGE}?rtnUrl={CPP_HOST}/&vendorLogin=false"
            log.debug("[CPP] 로그인 페이지 이동")
            page.goto(login_url, wait_until="domcontentloaded", timeout=20000)

            # 3) 이메일/비밀번호 입력
            page.wait_for_selector(
                "input[name='email'], input[type='email'], #email",
                timeout=10000
            )
            page.fill("input[name='email'], input[type='email'], #email", username)
            page.fill("input[name='password'], input[type='password'], #password", password)

            # 4) 로그인 버튼 클릭
            page.click(
                "button[type='submit'], input[type='submit'], "
                ".login-btn, #btnLogin, .btn-login"
            )

            # 5) 결과 대기
            try:
                page.wait_for_url(
                    lambda url: "login.coupang.com/login" not in url,
                    timeout=15000
                )
            except PWTimeout:
                pass

            final_url = page.url
            log.debug(f"[CPP] 최종 URL: {final_url}")

            # 6) 실패 판정
            if "login.coupang.com/login" in final_url:
                err_el = page.query_selector(".error-message, .alert, .error, #errMsg")
                msg = err_el.inner_text().strip() if err_el else "아이디 또는 비밀번호 오류"
                log.error(f"[CPP] 로그인 실패: {msg}")
                browser.close()
                return False

            if any(x in final_url.lower() for x in ("otp", "verify", "2fa", "security")):
                log.error("[CPP] 2단계 인증 필요 - 자동 로그인 불가")
                browser.close()
                return False

            # 7) 쿠키 추출 -> requests.Session 에 주입
            cookies = ctx.cookies()
            log.info(f"[CPP] 브라우저 로그인 성공, 쿠키 {len(cookies)}개 추출")
            for ck in cookies:
                domain = ck.get("domain", ".coupang.com")
                if not domain.startswith("."):
                    domain = "." + domain
                s.cookies.set(ck["name"], ck["value"], domain=domain)

            browser.close()
            return True

        except Exception as e:
            log.error(f"[CPP] Playwright 오류: {e}\n{traceback.format_exc()}")
            browser.close()
            return False


def _user_info(s):
    for ep in ["/api/v1/user/me", "/api/v1/auth/userinfo", "/api/v1/members/me"]:
        try:
            r = s.get(CPP_HOST + ep,
                      headers={"Accept": "application/json", "Referer": CPP_HOST + "/"},
                      timeout=10)
            if r.status_code == 200: return r.json()
        except Exception: pass
    return {}


def _profiles(s):
    for ep in ["/api/v1/profiles", "/api/v1/user/profiles"]:
        try:
            r = s.get(CPP_HOST + ep,
                      headers={"Accept": "application/json", "Referer": CPP_HOST + "/"},
                      timeout=10)
            if r.status_code != 200: continue
            d = r.json()
            if isinstance(d, list): return d
            for k in ("profiles", "data", "items"):
                if isinstance(d.get(k), list): return d[k]
        except Exception: pass
    return []


def _select_profile(s, idx):
    ps = _profiles(s)
    if not ps: return
    idx = max(0, min(int(idx), len(ps)-1))
    pid = ps[idx].get("profileId") or ps[idx].get("id")
    if not pid: return
    for ep in [f"/api/v1/profiles/{pid}/select", f"/api/v1/user/profiles/{pid}/select"]:
        try:
            r = s.post(CPP_HOST + ep, json={},
                       headers={"Accept": "application/json", "Content-Type": "application/json",
                                "Referer": CPP_HOST + "/"},
                       timeout=10)
            if r.status_code in (200, 201, 204): return
        except Exception: pass


def _restore(s, token):
    for n, v in token.get("cookies", {}).items():
        for d in [".coupangplay.com", ".coupang.com"]:
            s.cookies.set(n, v, domain=d)


def _build_token(s, user):
    ck = {c.name: c.value for c in s.cookies}
    now = int(time.time())
    return {
        "SESSION": {
            "bm_sv": ck.get("bm_sv", ""),
            "bm_sv_expires": now + 6 * 3600,
        },
        "cookies": ck, "user": user, "ts": now,
    }


# ═══════════════════════════════════════════════════════
#  채널 캐시
# ═══════════════════════════════════════════════════════

_CH_CACHE = []
_CH_TS = 0.0
_CH_TTL = 300


def _fetch_channels(force=False):
    global _CH_CACHE, _CH_TS
    log = _log()
    now = time.time()
    if not force and _CH_CACHE and (now - _CH_TS) < _CH_TTL:
        return _CH_CACHE

    s = _sess()
    use_live = _cfg("use_live", "False").lower() == "true"
    use_news = _cfg("use_news", "True").lower() == "true"
    chs = []

    spec = []
    if use_live: spec += [("/api/v1/live/channels", "LIVE"), ("/api/v2/live/channels", "LIVE")]
    if use_news: spec += [("/api/v1/live/news", "NEWS"), ("/api/v1/news/channels", "NEWS")]

    done_types = set()
    for ep, typ in spec:
        if typ in done_types: continue
        try:
            r = s.get(CPP_HOST + ep,
                      headers={"Accept": "application/json", "Referer": CPP_HOST + "/"},
                      timeout=15)
            log.debug(f"[CPP] {ep} => {r.status_code}")
            if r.status_code != 200: continue
            d = r.json()
            items = d if isinstance(d, list) else None
            if items is None:
                for k in ("data", "channels", "items", "result"):
                    if isinstance(d.get(k), list): items = d[k]; break
            if not items: continue
            for it in items:
                cid  = str(it.get("channelId") or it.get("id") or it.get("channel_id", ""))
                name = it.get("channelName") or it.get("name") or it.get("channel_name", cid)
                cp   = it.get("currentProgram") or it.get("nowPlaying") or {}
                prg  = cp.get("title") or cp.get("name", "") if isinstance(cp, dict) else ""
                logo = it.get("thumbnail") or it.get("thumbnailUrl") or it.get("logo", "")
                chs.append({"type": typ, "channel_id": cid, "channel_name": name,
                            "current_program": prg, "thumbnail": logo})
            done_types.add(typ)
        except Exception as e:
            log.error(f"[CPP] {ep} err: {e}")

    _CH_CACHE, _CH_TS = chs, now
    log.debug(f"[CPP] 채널 {len(chs)}개")
    return chs


def _stream_url(cid, token):
    log = _log()
    s = _sess()
    quality = _cfg("use_quality", "1920x1080")
    if token: _restore(s, token)
    for ep in [f"/api/v1/live/channels/{cid}/stream",
               f"/api/v2/live/channels/{cid}/stream",
               f"/api/v1/live/channels/{cid}/play"]:
        try:
            r = s.get(CPP_HOST + ep,
                      params={"quality": quality, "deviceType": "PC"},
                      headers={"Accept": "application/json", "Referer": CPP_HOST + "/live"},
                      timeout=15)
            log.debug(f"[CPP] stream {ep} => {r.status_code}")
            if r.status_code != 200: continue
            d = r.json()
            for k in ("streamUrl", "hlsUrl", "url", "playUrl"):
                v = d.get(k)
                if v: return v
            inner = d.get("data") or d.get("result") or {}
            if isinstance(inner, dict):
                for k in ("streamUrl", "hlsUrl", "url"):
                    v = inner.get(k)
                    if v: return v
        except Exception as e:
            log.error(f"[CPP] stream err: {e}")
    return ""


# ═══════════════════════════════════════════════════════
#  CPP_Handler
# ═══════════════════════════════════════════════════════

class CPP_Handler:

    @staticmethod
    def login(username, password, userprofile="0"):
        log = _log()
        log.info(f"[CPP] login: {username}")
        if not username or not password:
            log.error("[CPP] 아이디/패스워드 없음"); return {}
        if not _do_login(username, password): return {}
        s = _sess()
        user = _user_info(s)
        try: _select_profile(s, int(userprofile or 0))
        except Exception: pass
        token = _build_token(s, user)
        log.info("[CPP] 토큰 생성 완료")
        return token

    @staticmethod
    def get_cp_profile(profile_idx, token):
        log = _log()
        log.info("[CPP] 세션 갱신")
        s = _sess()
        _restore(s, token)
        user = _user_info(s)
        if not user:
            log.warning("[CPP] 세션 만료, 재로그인")
            un = _cfg("username"); pw = _cfg("password")
            if un and pw: return CPP_Handler.login(un, pw, profile_idx)
            return {}
        try: _select_profile(s, int(profile_idx or 0))
        except Exception: pass
        return _build_token(s, user)

    @staticmethod
    def ch_list():
        return _fetch_channels()

    @staticmethod
    def schedule_list():
        s = _sess()
        for ep in ["/api/v1/live/schedule", "/api/v1/epg/schedule"]:
            try:
                r = s.get(CPP_HOST + ep,
                          headers={"Accept": "application/json"}, timeout=15)
                if r.status_code == 200:
                    d = r.json()
                    return d if isinstance(d, list) else d.get("data", [])
            except Exception: pass
        return []

    @staticmethod
    def url_m3u8(req, token):
        log = _log()
        cid = req.args.get("channel_id", "")
        if not cid: return Response("channel_id required", status=400)
        url = _stream_url(cid, token)
        if not url:
            log.error(f"[CPP] stream url not found: {cid}")
            return Response("stream not found", status=404)
        if _cfg("streaming_type", "proxy") == "direct":
            return Response(f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000000\n{url}\n",
                            content_type="application/x-mpegURL")
        return redirect(url)

    @staticmethod
    def play(req):
        return CPP_Handler.url_m3u8(req, None)

    @staticmethod
    def segment(req):
        log = _log()
        url = req.args.get("url", "")
        if not url: return Response("url required", status=400)
        try:
            r = _sess().get(url, stream=True, timeout=30)
            return Response(r.iter_content(131072),
                            content_type=r.headers.get("Content-Type", "video/MP2T"),
                            headers={"Cache-Control": "no-cache"})
        except Exception as e:
            log.error(f"[CPP] segment: {e}")
            return Response("error", status=500)

    @staticmethod
    def make_m3u():
        chs = _fetch_channels()
        pkg = _PLUGIN.package_name if _PLUGIN else "ff_cpp_test"
        lines = ["#EXTM3U"]
        for ch in chs:
            cid  = ch["channel_id"]
            name = ch["channel_name"]
            logo = ch["thumbnail"]
            grp  = ch["type"]
            lines.append(
                f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{name}" '
                f'tvg-logo="{logo}" group-title="{grp}",{name}'
            )
            if _PLUGIN:
                from tool import ToolUtil
                u = ToolUtil.make_apikey_url(f"/{pkg}/api/url.m3u8?channel_id={cid}")
            else:
                u = f"/{pkg}/api/url.m3u8?channel_id={cid}"
            lines.append(u)
        return Response("\n".join(lines), content_type="application/x-mpegURL; charset=utf-8")

    @staticmethod
    def make_yaml():
        chs = _fetch_channels()
        lines = []
        for ch in chs:
            lines.append(f"- channel_id: \"{ch['channel_id']}\"")
            lines.append(f"  channel_name: \"{ch['channel_name']}\"")
            lines.append(f"  type: \"{ch['type']}\"")
        return Response("\n".join(lines), content_type="text/yaml; charset=utf-8")

    @staticmethod
    def sync_yaml_data():
        _fetch_channels(force=True)
