import requests
import json
import hashlib
import time
import sys
import os
import re
import base64
import urllib.parse

# 添加父目录到路径以便导入utils模块
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from app.utils.ip_helper import IPHelper
from app.utils.logger import logger
from app.utils.card_logger import card_log

# 重试配置常量
MAX_RETRY_COUNT = 10  # 最大重试次数
RETRY_DELAY = 2  # 重试间隔（秒）


class GameService:
    """充值服务类"""

    def __init__(self, cookies, gameid, task_no, proxies=None, use_proxy=False, proxy_city=None, card_key=None):
        """
        初始化充值程序
        :param cookies: 包含sessionid, userid, peerid的字典
        :param gameid: 游戏ID
        :param task_no: 任务ID
        :param proxies: 代理设置（可选）
        :param use_proxy: 是否使用代理（默认False）
        :param proxy_city: 代理城市名称，如'厦门'、'广州'等（默认None，使用广州）
        :param card_key: 卡密（用于日志标识）
        """
        from app.core.config import settings

        self.cookies = cookies
        self.gameid = gameid
        self.task_no = task_no
        self.userid = cookies.get('userid', '')
        self.peerid = cookies.get('peerid', 'CE3757F9E63020B8')
        self.use_proxy = use_proxy
        self.card_key = card_key or 'Unknown'  # 卡密标识

        # 确保proxy_city有值（如果为空则使用默认代理地区）
        if not proxy_city or proxy_city.strip() == '':
            self.proxy_city = settings.DEFAULT_PROXY_AREA
        else:
            self.proxy_city = proxy_city

        # 如果启用代理，获取代理配置
        if use_proxy:
            self.proxies = IPHelper.get_ip(area=self.proxy_city)
        else:
            self.proxies = proxies

        self.referer = f"https://wap-youxi.xunlei.com/gamerun?actfrom=playandgetxlvip2_mobi&aidfrom=sl_tap_banner_playandgetxlvip&gameid={self.gameid}"
        self.api_headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'origin': 'https://wap-youxi.xunlei.com',
            'referer': self.referer,
            'priority': 'u=1, i',
            'sec-ch-ua': '"Not;A=Brand";v="24", "Chromium";v="128"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'x-runtime': '1'
        }

        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self.session.headers.update(self.api_headers)
        if self.proxies:
            self.session.proxies.update(self.proxies)

        self.heartbeat_count = 0
        self.start_time = None
        self.heartbeat_rate = 0  # 心跳速率（次/分钟）
        self.heartbeat_times = []  # 存储心跳时间戳
        self.total_online_time = 0  # 累计在线时长（秒）
        self.last_heartbeat_time = None  # 上次心跳时间
        self._current_idcard_id = None  # 当前使用的身份证ID（实例变量，避免并发冲突）
        self.load_online_time()  # 加载之前的累计在线时长

    def _log(self, message, level='info'):
        """统一日志输出，同时写入全局日志和卡密专属日志"""
        proxy_info = f"代理:{self.proxy_city}" if self.use_proxy and self.proxy_city else "无代理"
        log_msg = f"[{self.card_key}][{proxy_info}] {message}"
        if level == 'error':
            logger.error(log_msg)
        elif level == 'warning':
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        if self.card_key and self.card_key != 'Unknown':
            card_log(self.card_key, message, level, proxy_info)

    def load_online_time(self):
        """加载之前的累计在线时长"""
        try:
            import os
            time_file = f"online_time_{self.userid}_{self.gameid}.txt"
            if os.path.exists(time_file):
                with open(time_file, 'r') as f:
                    content = f.read().strip()
                    if content.isdigit():
                        self.total_online_time = int(content)
                        self._log(f"加载累计在线时长: {self.total_online_time}秒")
        except Exception as e:
            self._log(f"加载在线时长失败: {e}", 'error')

    def save_online_time(self):
        """保存累计在线时长"""
        try:
            import os
            time_file = f"online_time_{self.userid}_{self.gameid}.txt"
            with open(time_file, 'w') as f:
                f.write(str(int(self.total_online_time)))
        except Exception as e:
            self._log(f"保存在线时长失败: {e}", 'error')

    def update_heartbeat_rate(self):
        """更新心跳速率"""
        current_time = time.time()
        # 保留最近60秒内的心跳时间戳
        self.heartbeat_times = [t for t in self.heartbeat_times if current_time - t <= 60]
        self.heartbeat_times.append(current_time)
        # 计算每分钟心跳次数
        self.heartbeat_rate = len(self.heartbeat_times)
        return self.heartbeat_rate

    def update_online_time(self):
        """更新在线时长"""
        current_time = time.time()
        if self.last_heartbeat_time:
            # 计算本次心跳与上次心跳的时间差
            time_diff = current_time - self.last_heartbeat_time
            # 限制单次增加的时间，防止异常情况
            if 0 < time_diff < 300:  # 最多5分钟
                self.total_online_time += time_diff
        self.last_heartbeat_time = current_time
        # 每30秒保存一次在线时长
        if int(current_time) % 30 == 0:
            self.save_online_time()
        return self.total_online_time

    def check_vip_eligibility(self):
        """检查会员领取资格"""
        # 条件1：累计心跳次数达到或超过65次
        if self.heartbeat_count >= 65:
            self._log(f"满足会员领取条件：累计心跳次数 {self.heartbeat_count}次")
            return True, f"累计心跳次数达到 {self.heartbeat_count}次"

        # 条件2：累计在线时长达到或超过10分钟
        if self.total_online_time >= 600:  # 10分钟 = 600秒
            minutes = int(self.total_online_time / 60)
            seconds = int(self.total_online_time % 60)
            self._log(f"满足会员领取条件：在线时长 {minutes}分{seconds}秒")
            return True, f"在线时长达到 {minutes}分{seconds}秒"

        # 未满足条件
        minutes = int(self.total_online_time / 60)
        seconds = int(self.total_online_time % 60)
        self._log(f"未满足会员领取条件：累计心跳次数 {self.heartbeat_count}次，在线时长 {minutes}分{seconds}秒")
        return False, f"累计心跳次数 {self.heartbeat_count}次，在线时长 {minutes}分{seconds}秒"

    def update_proxy(self, city=None):
        """
        更新代理IP，先清除缓存再获取新代理
        :param city: 指定城市名称，如果为None则使用初始化时的城市
        """
        if not self.use_proxy:
            self._log("未启用代理功能", 'warning')
            return False

        target_city = city if city is not None else self.proxy_city
        try:
            IPHelper.clear_cache(area=target_city)
            new_proxies = IPHelper.get_ip(area=target_city)
            if new_proxies:
                self.proxies = new_proxies
                self.session.proxies.update(new_proxies)
                self._log(f"代理已更新: {target_city}")
                return True
            else:
                self._log("获取代理失败", 'warning')
                return False
        except Exception as e:
            self._log(f"更新代理异常: {e}", 'error')
            return False

    def realname_status(self):
        """查询实名认证状态，无限重试直到成功"""
        url = 'https://youxi.xunlei.com/api/gamebox/v1/antiaddiction/realname/status'
        params = {
            'gameId': self.gameid,
        }

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.get(url, params=params, timeout=10)
                return response.json()
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._log(f"查询实名状态失败 (尝试 {attempt}): {type(e).__name__}", 'warning')
                self._log("立即更换代理")
                self.update_proxy()
            except Exception as e:
                self._log(f"查询实名状态异常 (尝试 {attempt}): {e}", 'error')
                time.sleep(1)

    @staticmethod
    def _get_idcard_from_db():
        """从数据库获取使用次数最少的启用身份证"""
        from app.core.database import SessionLocal
        from app.models.database import IdCard

        db = SessionLocal()
        try:
            # 选择状态为启用(1)且使用次数最少的身份证
            idcard = db.query(IdCard).filter(
                IdCard.status == 1
            ).order_by(
                IdCard.use_count.asc()
            ).first()

            if idcard:
                return (idcard.id, idcard.name, idcard.idcard)
            return None
        except Exception as e:
            logger.error(f"[身份证] 从数据库获取失败: {e}")
            return None
        finally:
            db.close()

    @staticmethod
    def _update_idcard_stats(idcard_id: int, success: bool):
        """更新身份证使用统计"""
        from app.core.database import SessionLocal
        from app.models.database import IdCard
        from app.utils.time_helper import get_beijing_date

        db = SessionLocal()
        try:
            idcard = db.query(IdCard).filter(IdCard.id == idcard_id).first()
            if idcard:
                idcard.use_count += 1
                idcard.last_used_at = get_beijing_date()
                if success:
                    idcard.success_count += 1
                else:
                    idcard.fail_count += 1
                db.commit()
                logger.info(f"[身份证] 更新统计: id={idcard_id}, 成功={success}")
        except Exception as e:
            db.rollback()
            logger.error(f"[身份证] 更新统计失败: {e}")
        finally:
            db.close()

    def realname_bind(self, name=None, idcard_no=None):
        """
        实名认证绑定
        :param name: 姓名，如果为None则从数据库选择使用次数最少的
        :param idcard_no: 身份证号，如果为None则从数据库选择
        :return: True 成功, False 失败
        """
        # 如果未指定，则从数据库获取使用次数最少的身份证
        if name is None or idcard_no is None:
            idcard_info = self._get_idcard_from_db()
            if not idcard_info:
                self._log("实名认证 - 数据库中无可用身份证，无法认证", 'error')
                return False
            self._current_idcard_id, name, idcard_no = idcard_info

        url = 'https://youxi.xunlei.com/api/gamebox/v1/antiaddiction/realname/bind'

        data = {
            'gameId': self.gameid,
            'name': name,
            'idcardNo': idcard_no,
        }

        # 先查询实名状态
        self._log("实名认证 - 开始查询状态")
        sfz_status = self.realname_status()
        if not sfz_status:
            self._log("实名认证 - 无法查询状态，跳过", 'warning')
            return False

        status_value = sfz_status.get('data', {}).get('status', 'unknown')
        self._log(f"实名认证 - 状态: {status_value}")

        # 只有status=0才是已通过，其他状态（-1未实名, 2认证失败等）都需要重新认证
        if 'data' in sfz_status and status_value != 0:
            self._log(f"实名认证 - 需要认证(status={status_value})，使用身份: {name}, 身份证: {idcard_no[:6]}****{idcard_no[-4:]}")
            attempt = 0
            while True:
                attempt += 1
                try:
                    response = self.session.post(url, json=data, timeout=10)
                    result = response.json()

                    # 检查响应code
                    code = result.get('code', -1)
                    if code == 0:
                        self._log("实名认证 - 成功")
                        # 更新身份证统计（成功）
                        if self._current_idcard_id:
                            self._update_idcard_stats(self._current_idcard_id, True)
                        return True
                    elif code == 3100:
                        # 认证频率过快，等待后重试
                        wait_time = 10 + (attempt % 5) * 5
                        self._log(f"实名认证 - 频率过快，{wait_time}秒后重试 (尝试 {attempt})", 'warning')
                        time.sleep(wait_time)
                        continue
                    else:
                        # 其他错误，打印完整响应内容便于调试
                        self._log(f"实名认证 - 失败 code={code}: {result.get('message', '未知错误')}", 'error')
                        self._log(f"实名认证 - 响应内容: {response.text}", 'error')
                        # 更新身份证统计（失败）
                        if self._current_idcard_id:
                            self._update_idcard_stats(self._current_idcard_id, False)
                        return False

                except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                    self._log(f"实名认证 - 网络失败 (尝试 {attempt}): {type(e).__name__}", 'warning')
                    self._log("实名认证 - 更换代理")
                    self.update_proxy()
                except Exception as e:
                    self._log(f"实名认证 - 异常: {e}", 'error')
                    return False
        else:
            # status=0 表示已实名认证通过
            self._log(f"实名认证 - 已通过 (status={status_value})")
            return True

    def play(self):
        """查询游戏进度，无限重试直到成功"""
        url = 'https://act-youxi.xunlei.com/api/iface'

        params = {
            'action': 'loginInit',
            'platform': 'mobi_game_center',
            'actno': 'playandgetxlvip2_mobi',
        }

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    response_json = response.json()

                    # 检查响应格式
                    if 'data' not in response_json:
                        self._log(f"进度查询 - 响应格式错误", 'error')
                        return

                    if 'task' not in response_json['data']:
                        self._log(f"进度查询 - 缺少task数据", 'error')
                        return

                    task = response_json['data']['task']
                    if self.task_no not in task:
                        self._log(f"进度查询 - 任务编号不存在: {self.task_no}", 'error')
                        return
                    self._log("taskid:" + self.task_no)
                    # with open("response_json.txt", "w") as f:
                        # f.write(str(response_json))

                    task_data = task[self.task_no]
                    conditions_list = task_data['conditions']
                    for conditions in conditions_list:
                        # conditions = task_data['conditions'][0]
                        cur_value = conditions['cur_value']
                        value = conditions['value']
                        if value == 0:
                            continue
                        jindu = cur_value / value * 100
                    self._log(f"进度查询 - 当前: {jindu:.1f}% (task_no={self.task_no}, gameid={self.gameid}, cur_value={cur_value}, value={value})")
                    if jindu >= 100:
                        lq_status = self.get_vip(self.task_no)
                        if lq_status == True:
                            self._log("进度查询 - 任务完成，VIP领取成功")
                            return {'completed': True, 'progress': 100, 'cur_value': cur_value, 'value': value}
                        elif lq_status == 'ACCOUNT_RISK':
                            self._log("进度查询 - 账号风险，无法领取VIP", 'error')
                            return {'completed': 'ACCOUNT_RISK', 'progress': 100, 'error': '账号存在风险', 'cur_value': cur_value, 'value': value}
                        elif isinstance(lq_status, dict):
                            error_msg = lq_status.get('error', 'VIP领取失败')
                            if '任务未完成' in error_msg or '请先完成任务' in error_msg:
                                self._log(f"进度查询 - VIP领取提示任务未完成，继续挂机", 'warning')
                                return {'completed': False, 'progress': 100, 'continue_heartbeat': True, 'cur_value': cur_value, 'value': value}
                            self._log(f"进度查询 - VIP领取失败: {error_msg}", 'error')
                            return {'completed': False, 'progress': 100, 'error': error_msg, 'cur_value': cur_value, 'value': value}
                    return {'completed': False, 'progress': int(jindu), 'cur_value': cur_value, 'value': value}
                elif response.status_code == 401:
                    self._log(f"进度查询 - HTTP 401 认证失败，登录已过期", 'error')
                    return {'completed': False, 'progress': 0, 'error': '登录已过期，请重新登录', 'auth_failed': True}
                else:
                    self._log(f"进度查询 - HTTP {response.status_code}，1秒后重试", 'warning')
                    time.sleep(1)
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._log(f"进度查询 - 网络失败 (尝试 {attempt}): {type(e).__name__}", 'warning')
                self._log("进度查询 - 更换代理")
                self.update_proxy()
            except Exception as e:
                self._log(f"进度查询 - 异常: {e}", 'error')
                time.sleep(1)

    def _md5_sign(self, params, secret_key='4mm29wexsp3k8pru9ocs'):
        """生成MD5签名"""
        # 参数排序
        sorted_params = sorted(params.items())
        # 拼接字符串
        param_str = '&'.join([f"{k}={v}" for k, v in sorted_params])
        # 添加密钥
        param_str += f"&key={secret_key}"
        # MD5加密并转大写
        md5_hash = hashlib.md5(param_str.encode('utf-8'))
        return md5_hash.hexdigest().upper()

    def get_game_info(self):
        """获取游戏信息，返回游戏名称，带重试机制"""
        url = f'https://youxi.xunlei.com/api/gamebox/v1/game/{self.gameid}'
        params = {'platform': 'mobi_game_center'}

        for attempt in range(1, MAX_RETRY_COUNT + 1):
            try:
                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    response_json = response.json()
                    if response_json.get('code') == 0:
                        game_data = response_json.get('data', {})
                        game_name = game_data.get('name', '未知游戏')
                        self._log(f"游戏信息 - 获取成功: {game_name} (gameid={self.gameid}, task_no={self.task_no})")
                        return game_name
                    self._log(f"游戏信息 - 获取失败", 'error')
                    return None
                else:
                    self._log(f"游戏信息 - HTTP {response.status_code}", 'warning')
                    time.sleep(RETRY_DELAY)
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._log(f"游戏信息 - 网络失败 (尝试 {attempt}/{MAX_RETRY_COUNT}): {type(e).__name__}", 'warning')
                self.update_proxy()
                if attempt >= MAX_RETRY_COUNT:
                    self._log(f"游戏信息 - 重试{attempt}次后失败", 'error')
                    return None
                time.sleep(RETRY_DELAY)
        return None

    def get_game_url(self):
        """
        获取游戏URL - 这一步很重要，会触发游戏开始，带重试机制

        Returns:
            True: 获取成功
            False: 获取失败（非实名问题）
            'REALNAME_ERROR': 因实名认证问题失败，可能需要等待后重试
        """
        url = 'https://youxi.xunlei.com/api/gamebox/v1/game/cp_url'
        params = {
            'platform': 'mobi_game_center',
            'game_id': self.gameid,
            'server_id': '',
            'referfrom': '',
            'actfrom': 'playandgetxlvip2_mobi'
        }

        max_retries = 5
        realname_error_count = 0

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    response_json = response.json()
                    code = response_json.get('code', -1)
                    if code == 0:
                        self.game_url = response_json.get('data', {}).get('url', '')
                        self._log(f"游戏URL - 获取成功")
                        return True
                    if code == 31001:
                        realname_error_count += 1
                        self._log(f"游戏URL - 实名认证未生效 (尝试 {attempt}/{max_retries})", 'warning')
                    else:
                        self._log(f"游戏URL - 获取失败 (尝试 {attempt}/{max_retries}), 响应: {response.text}", 'warning')
                    if attempt >= max_retries:
                        if realname_error_count == max_retries:
                            self._log(f"游戏URL - 因实名认证问题重试{max_retries}次后失败", 'error')
                            return 'REALNAME_ERROR'
                        self._log(f"游戏URL - 重试{max_retries}次后失败", 'error')
                        return False
                    time.sleep(RETRY_DELAY)
                else:
                    self._log(f"游戏URL - HTTP {response.status_code} (尝试 {attempt}/{max_retries})", 'warning')
                    time.sleep(RETRY_DELAY)
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._log(f"游戏URL - 网络失败 (尝试 {attempt}/{max_retries}): {type(e).__name__}", 'warning')
                self.update_proxy()
                if attempt >= max_retries:
                    self._log(f"游戏URL - 重试{max_retries}次后失败", 'error')
                    return False
                time.sleep(RETRY_DELAY)
        return False

    def start_game_report(self):
        """发送开始游戏上报 (reportType:1)"""
        url = 'https://youxi.xunlei.com/api/gamebox/v1/data/report'
        params = {'platform': 'mobi_game_center'}

        data = {
            'peerId': self.peerid,
            'runtime': 1,
            'platform': 'mobi_game_center',
            'reportType': 1,  # 1表示开始游戏
            'playGamePayload': {
                'gameId': self.gameid,
                'gameType': 2  # 2表示H5游戏
            }
        }

        response = self.session.post(url, params=params, json=data)
        if response.status_code == 200:
            response_json = response.json()
            if response_json.get('code') == 0:
                self._log("游戏上报 - 开始成功")
                return True
            self._log(f"游戏上报 - 失败", 'error')
            return False
        else:
            self._log(f"游戏上报 - HTTP {response.status_code}", 'error')
            return False

    def req_token_api(self, api_url):
        """
        调取外部api
        """
        token_str = ""
        self._log("调取api")
        for i in range(3):
            resp = self.session.get(api_url, timeout=10)
            resp_text = resp.text
            self._log("resp_text:" + resp_text)
            if "407" in resp_text:
                self._log("代理返回407，尝试更换代理", 'warning')
                self.update_proxy()
                time.sleep(1)
                continue
            if "securityToken" in resp_text:
                resp_dict = json.loads(resp_text)
                self._log("resp_dict:" + str(resp_dict))
                data = resp_dict.get("data")
                certifyId = data.get("certifyId")
                sceneId = data.get("sceneId")
                securityToken = data.get("securityToken")
                # 组合成{"certifyId":"h0v2dFgiZU","sceneId":"10mix91b","isSign":true,"securityToken":"6oOo7e72nA61uVLiZVKiLV5AXtY5dpKedrvJOKgMsFDht/nbrPAEksz/K9ksFHM4M1OMHI9Ly5w7lxodL+ShLqvwJG/ccPORsTfk5N+gwvn56NGZxtYVnpGPQU+OTmIv"}
                str_ = '"certifyId":"{}","sceneId":"{}","isSign":true,"securityToken":"{}"'.format(certifyId, sceneId, securityToken)
                str_ = "{" + str_ + "}"
                verify_param = base64.b64encode(str_.encode("utf-8"))
                verify_param = verify_param.decode('utf-8')
                self._log("verify_param:" + str(verify_param))
                resp_v = self.session.post("https://youxi.xunlei.com/api/gamebox/v1/heartbeat/token?platform=mobi_game_center", json={"scene_id": sceneId,"verify_param": verify_param})
                resp_text_v = resp_v.text
                self._log('resp_text_v:' + resp_text_v)
                token_str = re.findall(r'token":\s{0,1}"(.*?)"', resp_text_v)
                if not token_str:
                    self._log("心跳校验失败")
                    continue
                token_str = token_str[0]
                self._log("token:" + str([token_str, type(token_str)]))
                return token_str
            else:
                self._log("token获取失败：" + resp_text)
                time.sleep(1)
        return ""

    def get_token(self):

        # get_token_url = 'http://49.235.77.161:3023/api_aliv3_xunlei_27a7b3?iport='.format(ipport)
        # self._log(get_token_url)
        # for i in range(5):
        #     resp = requests.get(get_token_url.format(ipport))
        #     resp_text = resp.text
        #     self._log("心跳token：", resp_text)
        #     # token = dict(resp.text).get('verify_param')
        #     if "verify_param" in resp_text:
        #         securityToken = re.findall(r':\"(.*?)\"', resp_text)[0]
        #         self._log("securityToken:"+ securityToken)
        #         sceneId_info = base64.b64decode(securityToken).decode("utf-8")
        #         self._log("sceneId_info:" + sceneId_info)
        #         sceneId = re.findall(r'sceneId":"(.*?)"', sceneId_info)[0]
        #         self._log("sceneId:" + sceneId)

        #         resp = self.session.post("https://youxi.xunlei.com/api/gamebox/v1/heartbeat/token?platform=mobi_game_center", json={"scene_id": sceneId,"verify_param": securityToken})
        #         resp_text = resp.text
        #         token_str = re.findall(r'token":\s{0,1}"(.*?)"', resp_text)[0]
        #         break
        #     else:
        #         self._log("token获取失败：" + resp_text)
        #         time.sleep(1)
        get_token_url = "http://1.12.240.196:788/captcha/aliyun-v2?region=cn&prefix=sizq40&scene=10mix91b&is_verify=1&href=https%3A%2F%2Fwap-youxi.xunlei.com&proxy={}"
        get_token_url_ = "http://124.220.65.6:788/captcha/aliyun-v2?region=cn&prefix=sizq40&scene=10mix91b&is_verify=1&href=https%3A%2F%2Fwap-youxi.xunlei.com&proxy={}"
        proxy_candidates = []
        proxy_val = None
        if isinstance(self.proxies, dict):
            proxy_val = self.proxies.get('http') or self.proxies.get('https')
        if proxy_val:
            pv = str(proxy_val).strip().strip('`').strip().strip('"').strip("'")
            if pv.startswith('http://') or pv.startswith('https://'):
                try:
                    parsed = urllib.parse.urlparse(pv)
                    netloc = parsed.netloc or pv.replace('http://', '').replace('https://', '')
                except Exception:
                    netloc = pv.replace('http://', '').replace('https://', '')
                if netloc:
                    proxy_candidates.append(netloc)
                    proxy_candidates.append(f"http://{netloc}")
            else:
                proxy_candidates.append(pv)
                proxy_candidates.append(f"http://{pv}")
        ipinfo = re.findall(r'\d+\.\d+\.\d+\.\d+:\d+', str(self.proxies))
        if ipinfo:
            ipport = ipinfo[0]
            proxy_candidates.append(ipport)
            proxy_candidates.append(f"http://{ipport}")
        seen = set()
        ordered_candidates = []
        for c in proxy_candidates:
            if c and c not in seen:
                seen.add(c)
                ordered_candidates.append(c)
        if not ordered_candidates:
            self._log("ip获取失败")
            return ""
        token_str = ""
        for candidate in ordered_candidates:
            encoded = urllib.parse.quote(candidate, safe='')
            url1 = get_token_url.format(encoded)
            url2 = get_token_url_.format(encoded)
            self._log("api:" + url1)
            try:
                token_str = self.req_token_api(url1)
                if token_str:
                    break
                token_str = self.req_token_api(url2)
                if token_str:
                    break
            except Exception as e:
                self._log(f"获取token异常: {e}", 'warning')
                continue
        return token_str


    def send_heartbeat(self, token_str=''):
        # self._log(self.proxies)
        """发送心跳请求，无限重试直到成功"""
        if not token_str:
            return False

        url = 'https://youxi.xunlei.com/api/gamebox/v1/heartbeat'
        params = {
            'platform': 'mobi_game_center',
            'user_id': self.userid
        }

        timestamp = str(int(time.time()))

        # 构建签名参数
        sign_params = {
            'runtime': 1,
            'gameType': 2,
            'gameId': self.gameid,
            'platform': 'mobi_game_center',
            'timestamp': timestamp
        }

        # 生成签名
        key = self._md5_sign(sign_params)

        # 请求数据
        data = {
            'runtime': 1,
            'gameType': 2,
            'gameId': self.gameid,
            'platform': 'mobi_game_center',
            'timestamp': timestamp,
            # 'key': key,
            'referfrom': "ios_sybanner_playgamegetvip_ct_1",
            'token': token_str
        }

        for attempt in range(1, MAX_RETRY_COUNT + 1):
            try:
                response = self.session.post(url, params=params, json=data, timeout=10)
                if response.status_code == 200:
                    result = response.json()
                    if result.get('code') == 0:
                        self.heartbeat_count += 1
                        # 更新心跳速率
                        heartbeat_rate = self.update_heartbeat_rate()
                        # 更新在线时长
                        online_time = self.update_online_time()
                        # 检查会员领取资格
                        eligible, message = self.check_vip_eligibility()

                        elapsed = int(time.time() - self.start_time) if self.start_time else 0
                        self._log(f"心跳 - [{self.heartbeat_count}] 成功 | 在线: {elapsed//60}分{elapsed%60}秒 | 累计心跳: {self.heartbeat_count}次 | 累计在线: {int(online_time/60)}分{int(online_time%60)}秒")

                        if eligible:
                            self._log(f"🎉 满足会员领取条件: {message}")
                            # 自动执行会员领取流程
                            self._log("自动执行会员领取流程")
                            vip_result = self.get_vip(self.task_no)
                            if vip_result == True:
                                self._log("会员领取成功")
                            elif vip_result == 'ACCOUNT_RISK':
                                self._log("会员领取失败：账号存在风险", 'error')
                            elif isinstance(vip_result, dict):
                                self._log(f"会员领取失败：{vip_result.get('error', '未知错误')}", 'error')

                        return True
                    else:
                        self._log(f"心跳 - 失败: {result.get('msg', '未知错误')}", 'warning')
                        time.sleep(1)
                else:
                    self._log(f"心跳 - HTTP {response.status_code}", 'warning')
                    time.sleep(1)
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._log(f"心跳 - 网络失败 ({attempt}/{MAX_RETRY_COUNT}): {type(e).__name__}", 'warning')
                self.update_proxy()
                if attempt >= MAX_RETRY_COUNT:
                    self._log(f"心跳 - 重试{attempt}次后失败", 'error')
                    return False
                time.sleep(RETRY_DELAY)
            except Exception as e:
                self._log(f"心跳 - 异常: {e}", 'error')
                if attempt >= MAX_RETRY_COUNT:
                    return False
                time.sleep(1)
        return False

    def get_vip(self, task_no, realname_retry_count=0):
        """
        领取VIP奖励
        返回值:
            True: 领取成功
            dict: {'error': '错误信息', 'code': 错误码} - 领取失败
            'ACCOUNT_RISK': 账号存在风险

        Args:
            task_no: 任务编号
            realname_retry_count: 实名认证重试次数（内部使用，防止无限递归）
        """
        # 检查会员领取资格
        eligible, message = self.check_vip_eligibility()
        if not eligible:
            self._log(f"VIP领取 - 未满足领取条件: {message}", 'warning')
            return {'error': f'未满足会员领取条件: {message}', 'code': -2}

        url = "https://act-youxi.xunlei.com/api/iface"

        if "peerid" not in self.cookies:
            self.cookies['peerid'] = "CE3757F9E63020B8"

        params = {
            'action': 'finishedJob',
            'platform': 'mobi_game_center',
            'task_no': task_no,
            'actno': 'playandgetxlvip2_mobi'
        }

        max_realname_retry = 2  # 实名认证最大重试次数

        for attempt in range(1, MAX_RETRY_COUNT + 1):
            try:
                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    response_json = response.json()
                    response_code = response_json.get('code')
                    self._log(f"VIP领取 - 原始响应: code={response_code}, task_no={task_no}, response={response_json}")
                    if response_code == 0:
                        self._log(f"VIP领取 - 成功 (code=0, task_no={task_no})")
                        return True
                    elif response_code == 1001:
                        self._log(f"VIP领取 - 已领取 (code=1001, task_no={task_no})")
                        return True
                    elif response_code == 4:
                        error_msg = response_json.get('message', '账号存在风险')
                        self._log(f"VIP领取 - 账号风险 (code=4, task_no={task_no}): {error_msg}, 原始响应: {response_json}", 'error')
                        return 'ACCOUNT_RISK'
                    elif response_code == 10:
                        # code=10: 未实名认证，尝试重新实名认证后再领取
                        error_msg = response_json.get('message', '尚未防沉迷身份认证')
                        if realname_retry_count < max_realname_retry:
                            self._log(f"VIP领取 - 未实名认证，尝试重新认证 ({realname_retry_count + 1}/{max_realname_retry})", 'warning')
                            if self.realname_bind():
                                self._log(f"VIP领取 - 重新实名认证成功，等待5秒后重试领取")
                                time.sleep(5)
                                return self.get_vip(task_no, realname_retry_count + 1)
                            else:
                                self._log(f"VIP领取 - 重新实名认证失败", 'error')
                                return {'error': f'{error_msg}（重新认证失败）', 'code': response_code}
                        else:
                            self._log(f"VIP领取 - 实名认证重试次数已用尽", 'error')
                            return {'error': f'{error_msg}（认证重试{max_realname_retry}次后仍失败）', 'code': response_code}
                    else:
                        error_msg = response_json.get('message', response_json.get('msg', '未知错误'))
                        self._log(f"VIP领取 - 失败 (code={response_code}, task_no={task_no}): {error_msg}, 原始响应: {response_json}", 'error')
                        return {'error': error_msg, 'code': response_code}
                else:
                    self._log(f"VIP领取 - HTTP {response.status_code}", 'warning')
                    time.sleep(1)
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._log(f"VIP领取 - 网络失败 ({attempt}/{MAX_RETRY_COUNT}): {type(e).__name__}", 'warning')
                self.update_proxy()
                if attempt >= MAX_RETRY_COUNT:
                    self._log(f"VIP领取 - 重试{attempt}次后失败", 'error')
                    return {'error': f'网络异常，重试{attempt}次后失败', 'code': -1}
                time.sleep(RETRY_DELAY)
            except Exception as e:
                self._log(f"VIP领取 - 异常: {e}", 'error')
                if attempt >= MAX_RETRY_COUNT:
                    return {'error': str(e), 'code': -1}
                time.sleep(1)
        return {'error': '重试次数耗尽', 'code': -1}

    def run(self, heartbeat_interval=10, check_progress_interval=6):
        """
        运行充值流程
        :param heartbeat_interval: 心跳间隔（秒）
        :param check_progress_interval: 检查进度间隔（每N次心跳检查一次）
        """
        self._log("充值流程 - 开始")

        # 1. 防沉迷身份认证
        self.realname_bind()

        # 2. 获取游戏信息
        if not self.get_game_info():
            self._log("充值流程 - 获取游戏信息失败，退出", 'error')
            return False

        # 3. 获取游戏URL
        if not self.get_game_url():
            self._log("充值流程 - 获取游戏URL失败，退出", 'error')
            return False

        # 4. 发送开始游戏上报
        if not self.start_game_report():
            self._log("充值流程 - 游戏上报失败，退出", 'error')
            return False

        # 5. 初始化开始时间
        self.start_time = time.time()

        # 6. 查询初始进度
        self.play()

        # 7. 循环发送心跳
        self._log("充值流程 - 开始发送心跳")
        token_str = self.get_token()
        for i in range(1, 61):
            self.send_heartbeat(token_str)
            time.sleep(heartbeat_interval)

            # 定期检查进度
            if i % check_progress_interval == 0:
                self.play()

        self._log("充值流程 - 结束")
        return True
