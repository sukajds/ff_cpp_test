"""
source_cpp_handler.py
쿠팡플레이 핸들러 - curl_cffi 기반 로그인

[로그인 방식]
- curl_cffi 로 Chrome TLS 핑거프린트를 위장하여 Akamai 봇 차단 우회
- requests 단독 사용 시 403 차단됨
- playwright 는 Docker 환경에서 시스템 라이브러리 문제로 사용 불가
"""

import json, os, re, time, traceback, uuid
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
LOGIN_POST = f"{LOGIN_HOST}/login/loginProcess.pang"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
    if _g_sess is None:
        _g_sess = _new_sess()
    return _g_sess

def _new_sess():
    global _g_sess
    try:
        from curl_cffi.requests import Session
        s = Session(impersonate="chrome124")
    except ImportError:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        _log().warning("[CPP] curl_cffi 없음, requests 사용 (봇 차단될 수 있음)")
    _g_sess = s
    return s


# ═══════════════════════════════════════════════════════
#  로그인
# ═══════════════════════════════════════════════════════

def _do_login(username, password):
    log = _log()
    s = _new_sess()

    # 1) 쿠팡플레이 메인 → 초기 쿠키
    try:
        s.get(CPP_HOST + "/", timeout=15)
        log.debug("[CPP] 메인 접속 완료")
    except Exception as e:
        log.warning(f"[CPP] 메인 접속 오류: {e}")

    # 2) 로그인 페이지 → CSRF 토큰 추출
    try:
        r = s.get(
            LOGIN_PAGE,
            params={"rtnUrl": CPP_HOST + "/", "vendorLogin": "false"},
            timeout=15,
        )
        log.debug(f"[CPP] 로그인 페이지: {r.status_code}")
    except Exception as e:
        log.error(f"[CPP] 로그인 페이지 오류: {e}")
        return False

    csrf = ""
    for pat in [
        r'name=["\']_csrf["\'][^>]*value=["\']([^"\']+)["\']',
        r'value=["\']([^"\']+)["\'][^>]*name=["\']_csrf["\']',
        r'"_csrf"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pat, r.text)
        if m:
            csrf = m.group(1)
            log.debug(f"[CPP] CSRF 추출 성공: {csrf[:10]}...")
            break

    if not csrf:
        log.warning("[CPP] CSRF 토큰 없음 (없이 시도)")

    # 3) 로그인 POST
    form = {
        "email":       username,
        "password":    password,
        "rememberMe":  "true",
        "vendorLogin": "false",
        "rtnUrl":      CPP_HOST + "/",
    }
    if csrf:
        form["_csrf"] = csrf

    try:
        r = s.post(
            LOGIN_POST,
            data=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer":      LOGIN_PAGE,
                "Accept":       "text/html,application/xhtml+xml,*/*",
                "Origin":       LOGIN_HOST,
            },
            allow_redirects=True,
            timeout=20,
        )
        log.debug(f"[CPP] 로그인 POST: {r.status_code} => {r.url}")
    except Exception as e:
        log.error(f"[CPP] 로그인 POST 오류: {e}")
        return False

    # 4) 결과 판정
    if "login.coupang.com/login" in r.url:
        err_el = re.search(r'class=["\']error[^>]*>([^<]{3,80})', r.text)
        msg = err_el.group(1).strip() if err_el else "아이디 또는 비밀번호 오류"
        log.error(f"[CPP] 로그인 실패: {msg}")
        return False

    if any(x in r.url.lower() for x in ("otp", "verify", "2fa", "security")):
        log.error("[CPP] 2단계 인증 필요 - 자동 로그인 불가")
        return False

    cookies = [c for c in s.cookies]
    log.info(f"[CPP] 로그인 성공 (쿠키 {len(cookies)}개)")
    return True


def _user_info(s):
    for ep in ["/api/v1/user/me", "/api/v1/auth/userinfo", "/api/v1/members/me"]:
        try:
            r = s.get(
                CPP_HOST + ep,
                headers={"Accept": "application/json", "Referer": CPP_HOST + "/"},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return {}


def _profiles(s):
    for ep in ["/api/v1/profiles", "/api/v1/user/profiles"]:
        try:
            r = s.get(
                CPP_HOST + ep,
                headers={"Accept": "application/json", "Referer": CPP_HOST + "/"},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            d = r.json()
            if isinstance(d, list):
                return d
            for k in ("profiles", "data", "items"):
                if isinstance(d.get(k), list):
                    return d[k]
        except Exception:
            pass
    return []


def _select_profile(s, idx):
    ps = _profiles(s)
    if not ps:
        return
    idx = max(0, min(int(idx), len(ps) - 1))
    pid = ps[idx].get("profileId") or ps[idx].get("id")
    if not pid:
        return
    for ep in [f"/api/v1/profiles/{pid}/select", f"/api/v1/user/profiles/{pid}/select"]:
        try:
            r = s.post(
                CPP_HOST + ep, json={},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Referer": CPP_HOST + "/",
                },
                timeout=10,
            )
            if r.status_code in (200, 201, 204):
                return
        except Exception:
            pass


def _restore(s, token):
    for n, v in token.get("cookies", {}).items():
        for d in [".coupangplay.com", ".coupang.com"]:
            try:
                s.cookies.set(n, v, domain=d)
            except Exception:
                pass


def _build_token(s, user):
    try:
        ck = {c.name: c.value for c in s.cookies}
    except Exception:
        ck = dict(s.cookies)
    now = int(time.time())
    return {
        "SESSION": {
            "bm_sv":         ck.get("bm_sv", ""),
            "bm_sv_expires": now + 6 * 3600,
        },
        "cookies": ck,
        "user":    user,
        "ts":      now,
    }


# ═══════════════════════════════════════════════════════
#  채널 캐시
# ═══════════════════════════════════════════════════════

_CH_CACHE = []
_CH_TS    = 0.0
_CH_TTL   = 300


def _fetch_channels(force=False):
    global _CH_CACHE, _CH_TS
    log = _log()
    now = time.time()
    if not force and _CH_CACHE and (now - _CH_TS) < _CH_TTL:
        return _CH_CACHE

    s        = _sess()
    use_live = _cfg("use_live", "False").lower() == "true"
    use_news = _cfg("use_news", "True").lower()  == "true"
    chs      = []

    spec = []
    if use_live: spec += [("/api/v1/live/channels", "LIVE"), ("/api/v2/live/channels", "LIVE")]
    if use_news: spec += [("/api/v1/live/news",     "NEWS"), ("/api/v1/news/channels", "NEWS")]

    done_types = set()
    for ep, typ in spec:
        if typ in done_types:
            continue
        try:
            r = s.get(
                CPP_HOST + ep,
                headers={"Accept": "application/json", "Referer": CPP_HOST + "/"},
                timeout=15,
            )
            log.debug(f"[CPP] {ep} => {r.status_code}")
            if r.status_code != 200:
                continue
            d     = r.json()
            items = d if isinstance(d, list) else None
            if items is None:
                for k in ("data", "channels", "items", "result"):
                    if isinstance(d.get(k), list):
                        items = d[k]
                        break
            if not items:
                continue
            for it in items:
                cid  = str(it.get("channelId") or it.get("id") or it.get("channel_id", ""))
                name = it.get("channelName") or it.get("name") or it.get("channel_name", cid)
                cp   = it.get("currentProgram") or it.get("nowPlaying") or {}
                prg  = cp.get("title") or cp.get("name", "") if isinstance(cp, dict) else ""
                logo = it.get("thumbnail") or it.get("thumbnailUrl") or it.get("logo", "")
                chs.append({
                    "type": typ, "channel_id": cid, "channel_name": name,
                    "current_program": prg, "thumbnail": logo,
                })
            done_types.add(typ)
        except Exception as e:
            log.error(f"[CPP] {ep} err: {e}")

    _CH_CACHE, _CH_TS = chs, now
    log.debug(f"[CPP] 채널 {len(chs)}개")
    return chs


def _stream_url(cid, token):
    log     = _log()
    s       = _sess()
    quality = _cfg("use_quality", "1920x1080")
    if token:
        _restore(s, token)
    for ep in [
        f"/api/v1/live/channels/{cid}/stream",
        f"/api/v2/live/channels/{cid}/stream",
        f"/api/v1/live/channels/{cid}/play",
    ]:
        try:
            r = s.get(
                CPP_HOST + ep,
                params={"quality": quality, "deviceType": "PC"},
                headers={"Accept": "application/json", "Referer": CPP_HOST + "/live"},
                timeout=15,
            )
            log.debug(f"[CPP] stream {ep} => {r.status_code}")
            if r.status_code != 200:
                continue
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
            log.error("[CPP] 아이디/패스워드 없음")
            return {}
        if not _do_login(username, password):
            return {}
        s    = _sess()
        user = _user_info(s)
        try:
            _select_profile(s, int(userprofile or 0))
        except Exception:
            pass
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
            un = _cfg("username")
            pw = _cfg("password")
            if un and pw:
                return CPP_Handler.login(un, pw, profile_idx)
            return {}
        try:
            _select_profile(s, int(profile_idx or 0))
        except Exception:
            pass
        return _build_token(s, user)

    @staticmethod
    def ch_list():
        return _fetch_channels()

    @staticmethod
    def schedule_list():
        s = _sess()
        for ep in ["/api/v1/live/schedule", "/api/v1/epg/schedule"]:
            try:
                r = s.get(
                    CPP_HOST + ep,
                    headers={"Accept": "application/json"},
                    timeout=15,
                )
                if r.status_code == 200:
                    d = r.json()
                    return d if isinstance(d, list) else d.get("data", [])
            except Exception:
                pass
        return []

    @staticmethod
    def url_m3u8(req, token):
        log = _log()
        cid = req.args.get("channel_id", "")
        if not cid:
            return Response("channel_id required", status=400)
        url = _stream_url(cid, token)
        if not url:
            log.error(f"[CPP] stream url not found: {cid}")
            return Response("stream not found", status=404)
        if _cfg("streaming_type", "proxy") == "direct":
            return Response(
                f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000000\n{url}\n",
                content_type="application/x-mpegURL",
            )
        return redirect(url)

    @staticmethod
    def play(req):
        return CPP_Handler.url_m3u8(req, None)

    @staticmethod
    def segment(req):
        log = _log()
        url = req.args.get("url", "")
        if not url:
            return Response("url required", status=400)
        try:
            r = _sess().get(url, stream=True, timeout=30)
            return Response(
                r.iter_content(131072),
                content_type=r.headers.get("Content-Type", "video/MP2T"),
                headers={"Cache-Control": "no-cache"},
            )
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
