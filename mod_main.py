from datetime import datetime, timedelta
import json
import os
import time
from tool import ToolUtil
from flask import Response
from .setup import *

try:
    if os.path.exists(os.path.join(os.path.dirname(__file__), "source_cpp_handler.py")):
        from .source_cpp_handler import CPP_Handler

    else:
        from support import SupportSC

        CPP_Handler = SupportSC.load_module_f(__file__, "source_cpp_handler").CPP_Handler
except:
    pass


class ModuleMain(PluginModuleBase):
    def __init__(self, P):
        super(ModuleMain, self).__init__(P, name="main", first_menu="setting", scheduler_desc="쿠팡플레이")
        self.db_default = {
            f"{self.name}_db_version": "1",
            f"{self.name}_auto_start": "False",
            f"{self.name}_interval": "5",
            "plex_server_url": "http://localhost:32400",
            "plex_token": "",
            "plex_meta_item": "",
            "yaml_path": "",
            "use_live": "False",
            "use_news": "True",
            "use_quality": "1920x1080",
            "streaming_type": "proxy",
            "username": "",
            "password": "",
            "userprofile": "0",
            "device_id": "",
            "token_refresh_day": "5",
            "token": "",
            "token_time": "",
        }

    def process_menu(self, sub, req):
        arg = P.ModelSetting.to_dict()
        arg["api_m3u"] = ToolUtil.make_apikey_url(f"/{P.package_name}/api/m3u")
        arg["api_yaml"] = ToolUtil.make_apikey_url(f"/{P.package_name}/api/yaml")
        arg["hls_playback"] = "https://chrome.google.com/webstore/detail/native-hls-playback/emnphkkblegpebimobpbekeedfgemhof"
        if sub == "setting":
            arg["is_include"] = F.scheduler.is_include(self.get_scheduler_name())
            arg["is_running"] = F.scheduler.is_running(self.get_scheduler_name())
        return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)

    def process_command(self, command, arg1, arg2, arg3, req):
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        streaming_type = P.ModelSetting.get("streaming_type")

        if command == "broad_list":
            return jsonify({"list": CPP_Handler.ch_list(), "updated_at": updated_at, "streaming_type": streaming_type})
        elif command == "schedule_list":
            return jsonify({"list": CPP_Handler.schedule_list(), "updated_at": updated_at})
        elif command == "play_url":
            url = arg1
            ret = {"ret": "success", "data": url}
        elif command == "login_check":
            data = self.token_refresh(force=True)
            ret = {"ret": "success", "json": data}
        elif command == "token_delete":
            P.ModelSetting.set("token", "")
            P.ModelSetting.set("token_time", "")
            data = "OK"
            ret = {"ret": "success", "json": data}
        return jsonify(ret)

    def process_api(self, sub, req):
        try:
            if sub == "m3u":
                return CPP_Handler.make_m3u()
            elif sub == "yaml":
                return CPP_Handler.make_yaml()
            elif sub == "url.m3u8":
                token = self.token_refresh()
                return CPP_Handler.url_m3u8(req, token)
            elif sub == "play":
                return CPP_Handler.play(req)
            elif sub == "segment":
                return CPP_Handler.segment(req)
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())

    def scheduler_function(self):
        try:
            CPP_Handler.sync_yaml_data()
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())

    def token_refresh(self, force=False):
        flag = False
        form = "%Y-%m-%d %H:%M:%S"
        get_token = P.ModelSetting.get("token")
        if force:
            flag = True
        if flag == False and get_token == "":
            flag = True
        if flag == False and get_token:
            json_token = json.loads(get_token)
            if json_token["SESSION"]["bm_sv_expires"] < int(time.time()):
                data = CPP_Handler.get_cp_profile(P.ModelSetting.get("userprofile"), json_token)
                if data:
                    json_data = json.dumps(data)
                    P.ModelSetting.set("token", json_data)
                    P.logger.debug("bm_sv 만료 갱신")
        if flag == False:
            last_time_str = P.ModelSetting.get("token_time")
            if last_time_str == "":
                flag = True
            else:
                last_time = datetime.strptime(last_time_str, form)
                if last_time + timedelta(days=P.ModelSetting.get_int("token_refresh_day")) < datetime.now():
                    flag = True

        if flag:
            data = CPP_Handler.login(P.ModelSetting.get("username"), P.ModelSetting.get("password"), P.ModelSetting.get("userprofile"))
            if data:
                json_data = json.dumps(data)
                P.ModelSetting.set("token", json_data)
                P.ModelSetting.set("token_time", datetime.now().strftime(form))
            return data

        if get_token:
            return json.loads(get_token)
        else:
            return ""
