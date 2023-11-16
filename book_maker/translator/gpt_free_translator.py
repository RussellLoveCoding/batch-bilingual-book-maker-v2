import re
import time
from copy import copy
from os import environ
from rich import print
import logger


import argparse

from ..revedOpenai.api import ChatCompletion

from .base_translator import Base

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}

import requests
import uuid


HOST = "https://ai.fakeopen.com"
SHARE_TOKEN_URI = "/token/register"
ACESS_TOKEN_URI = "/auth/login"
POOL_TOKEN_URI = "/pool/update"
PROXY_URL = "http://192.168.3.3:7890"


class FakeOpenai():
    # 定义接口的URL
    def __init__(self) -> None:
        pass


    def genAccessToken(self, username, password): 
        """
        """
        # 定义请求头
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }


        # 定义请求参数
        payload = {
            "username": "",
            "password": "",
            # 如果开启了二次验证，需要提供 mfa_code
            # "mfa_code": "你的二次验证代码"
        }


        # 设置代理
        proxies = {
            "http": PROXY_URL,
            "https": PROXY_URL
        }


        response = requests.post(HOST+ACESS_TOKEN_URI, headers=headers, data=payload, proxies=proxies)


        # 检查响应是否成功
        if response.status_code == 200:
            print("登录成功！")
            # 解析响应内容，获取 Access Token 和 Refresh Token 等信息
            responseData = response.json()
            access_token = responseData.get("access_token")
            refresh_token = responseData.get("refresh_token")
            # 打印获取的信息
            print("Access Token:", access_token)
            print("Refresh Token:", refresh_token)
            return access_token
        else:
            print("登录失败，状态码:", response.status_code)
            return ""


    def genShareToken(self, access_token):
        """_summary_
        接口描述： 注册或更新 Share Token 。
        unique_name：一个唯一的名字，这里要注意相同 unique_name 和 access_token 始终生成相同的 Share Token 。
        access_token：ChatGPT 账号的 Access Token 。
        site_limit：限制 Share Token 使用的站点，格式为：https://xxx.yyy.com，可留空不作限制。
        expires_in：Share Token 的有效期，单位为：秒，为 0 时表示与 Access Token 同效，为 -1 时吊销 Share Token 。
        show_conversations：是否进行会话隔离，true 或 false ，默认为 false 。
        show_userinfo：是否隐藏 邮箱 等账号信息，true 或 false ，默认为 false 。

        返回字段： 返回 Share Token 等信息。 {'expire_at': 1696002304, 'show_conversations': True, 'show_userinfo': False, 'site_limit': '', 'token_key': '', 'unique_name': ''}
        频率控制： 无。
        """


        url = HOST + SHARE_TOKEN_URI
        headers = { "Content-Type": "application/x-www-form-urlencoded" }
        payload = {
            "unique_name": uuid.uuid4(),  
            "access_token": access_token, 
            "expires_in": 0,
            "show_conversations": "true", # 是否进行会话隔离，true 或 false ，默认为 false 。
            "show_userinfo": "false", # 是否隐藏 邮箱 等账号信息，true 或 false ，默认为 false 。
        }

        response = requests.post(url, headers=headers, data=payload, proxies=None)

        # 检查响应是否成功
        if response.status_code == 200:
            print("share token generated")
            # 解析响应内容，获取 Access Token 和 Refresh Token 等信息
            responseData = response.json()
            tokenKey = responseData.get("token_key")
            return tokenKey
        else:
            print("登录失败，状态码:", response.status_code)
            return ""


    def genPoolToken(self, shareTokens):
        """_summary_
        testKey: pk-O4R0FNszQrOpDLzjQg27k7GCasy-W-R3IZVcnARxCJ8

        /pool/update
        接口描述： 注册或更新 Pool Token 。
        HTTP方法： POST
        请求类型： application/x-www-form-urlencoded
        请求字段：
        share_tokens：Share Token 列表，每行 1 个，最多 100 个。
        pool_token：Pool Token ，可留空，留空时生成新 Pool Token 。不为空则更新 Pool Token 。
        返回字段： 返回 Pool Token 等信息。
        频率控制： 无。
        特别说明： share_tokens 为空，且 pool_token 不为空时，会吊销指定的 Pool Token 。
        """

        url = HOST + POOL_TOKEN_URI
        headers = { "Content-Type": "application/x-www-form-urlencoded" }
        
        shareToknsParam = ""
        for t in shareTokens:
            shareToknsParam += t + "\n"
        payload = {
            "share_tokens": shareToknsParam,
        }

        response = requests.post(url, headers=headers, data=payload, proxies=None)
        if response.status_code == 200:
            responseData = response.json()
            print("pool token generated: %s containg %d share accounts" % responseData.get("pool_token"), responseData.get("count"))
            return responseData.get("pool_token")
        else:
            logger.error("pool token generate failed")


class GPTFreeTranslator(Base):
    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        # TODO 不支持temperature参数，可以通过 prompt 加入
        temperature=1.0,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.api_key = key
        self.api = ChatCompletion(proxy=None)

        self.prompt_template = (
            prompt_template
            or environ.get(PROMPT_ENV_MAP["user"])
            or self.DEFAULT_PROMPT
        )
        self.prompt_sys_msg = (
            prompt_sys_msg
            or environ.get(
                "OPENAI_API_SYS_MSG",
            )  # XXX: for backward compatibility, deprecate soon
            or environ.get(PROMPT_ENV_MAP["system"])
            or ""
        )
        self.system_content = environ.get("OPENAI_API_SYS_MSG") or ""
        self.deployment_id = None
        self.temperature = temperature

    # 要修改的是这里
    def create_chat_completion(self, text):
        content = self.prompt_template.format(
            text=text, language=self.language, crlf="\n"
        )
        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")
        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": content},
        ]

        status, header, generator = self.api.request(
            api_key=self.api_key, 
            model="gpt-3.5-turbo-16k",
            messages=messages,
            stream=False
            )
        return next(generator)

    def get_translation(self, text):
        self.rotate_key()

        completion = {}
        try:
            completion = self.create_chat_completion(text)
        except Exception:
            if (
                "choices" not in completion
                or not isinstance(completion["choices"], list)
                or len(completion["choices"]) == 0
            ):
                raise
            if completion["choices"][0]["finish_reason"] != "length":
                raise

        # work well or exception finish by length limit
        choice = completion["choices"][0]

        t_text = choice.get("message").get("content", "").encode("utf8").decode()

        if choice["finish_reason"] == "length":
            with open("log/long_text.txt", "a") as f:
                print(
                    f"""==================================================
The total token is too long and cannot be completely translated\n
{text}
""",
                    file=f,
                )

        return t_text

    def translate(self, text, needprint=True):
        start_time = time.time()
        # todo: Determine whether to print according to the cli option
        if needprint:
            print(re.sub("\n{3,}", "\n\n", text))

        attempt_count = 0
        max_attempts = 3
        t_text = ""

        while attempt_count < max_attempts:
            try:
                t_text = self.get_translation(text)
                break
            except Exception as e:
                # todo: better sleep time? why sleep alawys about key_len
                # 1. openai server error or own network interruption, sleep for a fixed time
                # 2. an apikey has no money or reach limit, don`t sleep, just replace it with another apikey
                # 3. all apikey reach limit, then use current sleep
                sleep_time = 3 # int(60 / self.key_len)
                print(e, f"will sleep {sleep_time} seconds")
                time.sleep(sleep_time)
                attempt_count += 1
                if attempt_count == max_attempts:
                    print(f"Get {attempt_count} consecutive exceptions")
                    raise

        # todo: Determine whether to print according to the cli option
        if needprint:
            print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")

        time.time() - start_time
        # print(f"translation time: {elapsed_time:.1f}s")

        return t_text

    def translate_and_split_lines(self, text):
        result_str = self.translate(text, False)
        lines = result_str.splitlines()
        lines = [line.strip() for line in lines if line.strip() != ""]
        return lines

    def get_best_result_list(
        self,
        plist_len,
        new_str,
        sleep_dur,
        result_list,
        max_retries=15,
    ):
        if len(result_list) == plist_len:
            return result_list, 0

        best_result_list = result_list
        retry_count = 0

        while retry_count < max_retries and len(result_list) != plist_len:
            print(
                f"bug: {plist_len} -> {len(result_list)} : Number of paragraphs before and after translation",
            )
            print(f"sleep for {sleep_dur}s and retry {retry_count+1} ...")
            time.sleep(sleep_dur)
            retry_count += 1
            result_list = self.translate_and_split_lines(new_str)
            if (
                len(result_list) == plist_len
                or len(best_result_list) < len(result_list) <= plist_len
                or (
                    len(result_list) < len(best_result_list)
                    and len(best_result_list) > plist_len
                )
            ):
                best_result_list = result_list

        return best_result_list, retry_count

    def log_retry(self, state, retry_count, elapsed_time, log_path="log/buglog.txt"):
        if retry_count == 0:
            return
        print(f"retry {state}")
        with open(log_path, "a", encoding="utf-8") as f:
            print(
                f"retry {state}, count = {retry_count}, time = {elapsed_time:.1f}s",
                file=f,
            )

    def log_translation_mismatch(
        self,
        plist_len,
        result_list,
        new_str,
        sep,
        log_path="log/buglog.txt",
    ):
        if len(result_list) == plist_len:
            return
        newlist = new_str.split(sep)
        with open(log_path, "a", encoding="utf-8") as f:
            print(f"problem size: {plist_len - len(result_list)}", file=f)
            for i in range(len(newlist)):
                print(newlist[i], file=f)
                print(file=f)
                if i < len(result_list):
                    print("............................................", file=f)
                    print(result_list[i], file=f)
                    print(file=f)
                print("=============================", file=f)

        print(
            f"bug: {plist_len} paragraphs of text translated into {len(result_list)} paragraphs",
        )
        print("continue")

    def join_lines(self, text):
        lines = text.splitlines()
        new_lines = []
        temp_line = []

        # join
        for line in lines:
            if line.strip():
                temp_line.append(line.strip())
            else:
                if temp_line:
                    new_lines.append(" ".join(temp_line))
                    temp_line = []
                new_lines.append(line)

        if temp_line:
            new_lines.append(" ".join(temp_line))

        text = "\n".join(new_lines)

        # del ^M
        text = text.replace("^M", "\r")
        lines = text.splitlines()
        filtered_lines = [line for line in lines if line.strip() != "\r"]
        new_text = "\n".join(filtered_lines)

        return new_text

    def translate_list(self, plist):
        sep = "\n\n\n\n\n"
        # new_str = sep.join([item.text for item in plist])

        new_str = ""
        i = 1
        for p in plist:
            temp_p = copy(p)
            for sup in temp_p.find_all("sup"):
                sup.extract()
            new_str += f"({i}) {temp_p.get_text().strip()}{sep}"
            i = i + 1

        if new_str.endswith(sep):
            new_str = new_str[: -len(sep)]

        new_str = self.join_lines(new_str)

        plist_len = len(plist)

        print(f"plist len = {len(plist)}")

        result_list = self.translate_and_split_lines(new_str)

        start_time = time.time()

        result_list, retry_count = self.get_best_result_list(
            plist_len,
            new_str,
            6,
            result_list,
        )

        end_time = time.time()

        state = "fail" if len(result_list) != plist_len else "success"
        log_path = "log/buglog.txt"

        self.log_retry(state, retry_count, end_time - start_time, log_path)
        self.log_translation_mismatch(plist_len, result_list, new_str, sep, log_path)

        # del (num), num. sometime (num) will translated to num.
        result_list = [re.sub(r"^(\(\d+\)|\d+\.|(\d+))\s*", "", s) for s in result_list]
        return result_list