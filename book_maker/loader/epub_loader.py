import os
import time
import traceback
import json
import string
import sys
from copy import copy
from pathlib import Path
from functools import partial
from typing import Callable
import pickle

from bs4 import BeautifulSoup as bs
from bs4 import Tag
from bs4.element import NavigableString
from ebooklib import ITEM_DOCUMENT, epub
from rich import print
from tqdm import tqdm

from book_maker.utils import num_tokens_from_text, prompt_config_to_kwargs
from book_maker.utils import logger

from .base_loader import BaseBookLoader
from .helper import EPUBBookLoaderHelper, is_text_link, not_trans


PID_INDEX_OF_PARAGRAPH_NUM = 0 
PID_INDEX_OF_DOCITEM_ID = 1
PID_INDEX_OF_DOCITEM_FILE_NAME = 2
PID_INDEX_OF_DOCUMENT_NAME = 3

class EPUBBookLoader(BaseBookLoader):
    def __init__(
        self,
        epub_name="",
        model=None,
        key="",
        resume=False,
        language="en",
        model_api_base="",
        is_test=False,
        test_num=5,
        prompt_config=None,
        is_bilingual=False,
        context_flag=False,
        temperature=1.0,
    ):
        
        self.epub_name = epub_name
        self.is_test = is_test
        self.test_num = test_num
        self.translate_tags = "p"
        self.exclude_translate_tags = "sup"
        self.allow_navigable_strings = False
        self.accumulated_num = 1
        self.translation_style = ""
        self.context_flag = context_flag
        self.retranslate = None
        self.exclude_filelist = ""
        self.only_filelist = ""
        self.is_bilingual = is_bilingual

        if options.mode == "test":
            return
        self.new_epub = epub.EpubBook()
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            context_flag=context_flag,
            temperature=temperature,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
        )

        # monkey patch for # 173: fixed missing html tag <head>
        def _write_items_patch(obj):
            for item in obj.book.get_items():
                if isinstance(item, epub.EpubNcx):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), obj._get_ncx()
                    )
                elif isinstance(item, epub.EpubNav):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name),
                        obj._get_nav(item),
                    )
                elif item.manifest:
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), item.content
                    )
                else:
                    obj.out.writestr("%s" % item.file_name, item.content)

        epub.EpubWriter._write_items = _write_items_patch

        try:
            self.origin_book = epub.read_epub(self.epub_name)
        except Exception:
            # tricky monkey patch for #71 if you don't know why please check the issue and ignore this
            # when upstream change will TODO fix this
            def _load_spine(obj):
                spine = obj.container.find("{%s}%s" % (epub.NAMESPACES["OPF"], "spine"))

                obj.book.spine = [
                    (t.get("idref"), t.get("linear", "yes")) for t in spine
                ]
                obj.book.set_direction(spine.get("page-progression-direction", None))

            epub.EpubReader._load_spine = _load_spine
            self.origin_book = epub.read_epub(self.epub_name)

        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(epub_name).parent}/.{Path(epub_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return (
            text.isdigit()
            or text.isspace()
            or is_text_link(text)
            or all(char in string.punctuation for char in text)
        )

    def _make_new_book(self, book):
        new_book = epub.EpubBook()
        new_book.metadata = book.metadata
        new_book.spine = book.spine
        new_book.toc = book.toc
        return new_book

    def _process_paragraph(self, p, index, p_to_save_len):
        """
        这些过滤都是为了得到只需要翻译的文本，其实翻译引擎可以处理他们

        1. 过滤特殊字符、空段落, 
        2. 处理导航段落
        3. 翻译普通段落
        4. 保存翻译结果并在原段落后插入翻译结果
        """
        if not p.text or self._is_special_text(p.text):
            return index

        new_p = copy(p)

        for p_exclude in self.exclude_translate_tags.split(","):
            # for issue #280
            if type(p) == NavigableString:
                continue
            for pt in new_p.find_all(p_exclude):
                pt.extract()

        if self.resume and index < p_to_save_len:
            new_p.string = self.p_to_save[index]
        else:
            # 带有导航格式的 element, 这里就交给 translator 处理了
            if type(p) == NavigableString:
                new_p = self.translate_model.translate(new_p.text)
                self.p_to_save.append(new_p)
            # 纯文本
            else:
                new_p.string = self.translate_model.translate(new_p.text)
                self.p_to_save.append(new_p.text)

        self.helper.insert_trans(
            p, new_p.string, self.translation_style, self.is_bilingual
        )
        index += 1

        if index % 20 == 0:
            self._save_progress()

        return index

    def translate_paragraphs_acc(self, p_list, send_num):
        """

        提取出 paragraph, 复制出一个新的 p, 用来清洗提取，得到文本，拿去翻译，再将翻译文本
        插到原来的 p 的后面。

        增量翻译段落。
        openai 上下文大小 #token = #(input tokens) + #(output tokens)，所以一个段落可能
        的长度实际上会消耗两倍的 token数量，为避免截断，即漏翻译，所以采用增量翻译的方式。
        """
        count = 0
        wait_p_list = []
        for i in range(len(p_list)):
            p = p_list[i]
            
            # 此处的 tmp_p 看来只是为了统计字符用的
            temp_p = copy(p)

            # 导航字符
            # 出现错误'NavigableString' object has no attribute 'find_all' #280
            # Closed • Faintabc opened about 4 months ago • 1 comment
            for p_exclude in self.exclude_translate_tags.split(","):
                # for issue #280
                if type(p) == NavigableString:
                    continue
                for pt in temp_p.find_all(p_exclude):
                    pt.extract()

            # 处理空段落，
            if any(
                [not p.text, self._is_special_text(temp_p.text), not_trans(temp_p.text)]
            ):
                if i == len(p_list) - 1:
                    self.helper.deal_old(wait_p_list, self.is_bilingual)
                continue
            length = num_tokens_from_text(temp_p.text)
            
            # 直接翻译 长度大于 单个请求文本的长度  的 段落
            if length > send_num:
                self.helper.deal_new(p, wait_p_list, self.is_bilingual)
                continue

            # 对于段落文本长度小于 单个请求翻译的长度, 需要积累起来。
            # 处理最后一个段落
            if i == len(p_list) - 1:
                if count + length < send_num:
                    wait_p_list.append(p)
                    self.helper.deal_old(wait_p_list, self.is_bilingual)
                else:
                    self.helper.deal_new(p, wait_p_list, self.is_bilingual)
                break
            if count + length < send_num:
                count += length
                wait_p_list.append(p)
                # This is because the more paragraphs, the easier it is possible to translate different numbers of paragraphs, maybe you should find better values than 15 and 2
                # if len(wait_p_list) > 15 and count > send_num / 2:
                #     self.helper.deal_old(wait_p_list)
                #     count = 0
            else:
                self.helper.deal_old(wait_p_list, self.is_bilingual)
                wait_p_list.append(p)
                count = length

    def get_item(self, book, name):
        for item in book.get_items():
            if item.file_name == name:
                return item

    def find_items_containing_string(self, book, search_string):
        matching_items = []

        for item in book.get_items_of_type(ITEM_DOCUMENT):
            content = item.get_content().decode("utf-8")
            if search_string in content:
                matching_items.append(item)

        return matching_items

    # 重新翻译特定标签
    def retranslate_book(self, index, p_to_save_len, pbar, trans_taglist, retranslate):
        complete_book_name = retranslate[0]
        fixname = retranslate[1]
        fixstart = retranslate[2]
        fixend = retranslate[3]

        if fixend == "":
            fixend = fixstart

        name_fix = complete_book_name

        complete_book = epub.read_epub(complete_book_name)

        if fixname == "":
            fixname = self.find_items_containing_string(complete_book, fixstart)[
                0
            ].file_name
            print(f"auto find fixname: {fixname}")

        new_book = self._make_new_book(complete_book)

        complete_item = self.get_item(complete_book, fixname)
        if complete_item is None:
            return

        ori_item = self.get_item(self.origin_book, fixname)
        if ori_item is None:
            return

        soup_complete = bs(complete_item.content, "html.parser")
        soup_ori = bs(ori_item.content, "html.parser")

        p_list_complete = soup_complete.findAll(trans_taglist)
        p_list_ori = soup_ori.findAll(trans_taglist)

        target = None
        tagl = []

        # extract from range
        find_end = False
        find_start = False
        for tag in p_list_complete:
            if find_end:
                tagl.append(tag)
                break

            if fixend in tag.text:
                find_end = True
            if fixstart in tag.text:
                find_start = True

            if find_start:
                if not target:
                    target = tag.previous_sibling
                tagl.append(tag)

        for t in tagl:
            t.extract()

        flag = False
        extract_p_list_ori = []
        for p in p_list_ori:
            if fixstart in p.text:
                flag = True
            if flag:
                extract_p_list_ori.append(p)
            if fixend in p.text:
                break

        for t in extract_p_list_ori:
            if target:
                target.insert_after(t)
                target = t

        for item in complete_book.get_items():
            if item.file_name != fixname:
                new_book.add_item(item)
        if soup_complete:
            complete_item.content = soup_complete.encode()

        index = self.process_item(
            complete_item,
            index,
            p_to_save_len,
            pbar,
            new_book,
            trans_taglist,
            fixstart,
            fixend,
        )
        epub.write_epub(f"{name_fix}", new_book, {})

    def has_nest_child(self, element, trans_taglist):
        if isinstance(element, Tag):
            for child in element.children:
                if child.name in trans_taglist:
                    return True
                if self.has_nest_child(child, trans_taglist):
                    return True
        return False

    def filter_nest_list(self, p_list, trans_taglist):
        """
        过滤掉嵌套的 p , 即段落里还有个段落
        """
        filtered_list = [p for p in p_list if not self.has_nest_child(p, trans_taglist)]
        return filtered_list

    def process_item(
        self,
        item: epub.EpubItem,
        index,
        p_to_save_len,
        pbar,
        new_book,
        trans_taglist,
        fixstart=None,
        fixend=None,
    ):
        if self.only_filelist != "" and not item.file_name in self.only_filelist.split(
            ","
        ):
            return index
        elif self.only_filelist == "" and item.file_name in self.exclude_filelist.split(
            ","
        ):
            new_book.add_item(item)
            return index

        if not os.path.exists("log"):
            os.makedirs("log")

        # BeautifulSoup 是一个 Python 库，用于从 HTML 和 XML 文件中提取数据。它
        # 提供了一种简单的方式来遍历和搜索 HTML 和 XML 树，
        soup = bs(item.content, "html.parser")
        p_list = soup.findAll(trans_taglist)

        p_list = self.filter_nest_list(p_list, trans_taglist)

        if self.retranslate:
            new_p_list = []

            if fixstart is None or fixend is None:
                return

            start_append = False
            for p in p_list:
                text = p.get_text()
                if fixstart in text or fixend in text or start_append:
                    start_append = True
                    new_p_list.append(p)
                if fixend in text:
                    p_list = new_p_list
                    break

        if self.allow_navigable_strings:
            p_list.extend(soup.findAll(text=True))

        send_num = self.accumulated_num
        if send_num > 1:
            with open("log/buglog.txt", "a") as f:
                print(f"------------- {item.file_name} -------------", file=f)

            print("------------------------------------------------------")
            print(f"dealing {item.file_name} ...")
            self.translate_paragraphs_acc(p_list, send_num)
        else:
            is_test_done = self.is_test and index > self.test_num
            for p in p_list:
                if is_test_done:
                    break
                index = self._process_paragraph(p, index, p_to_save_len)
                # pbar.update(delta) not pbar.update(index)?
                pbar.update(1)
                print()
                if self.is_test and index >= self.test_num:
                    break

        if soup:
            item.content = soup.encode()
        new_book.add_item(item)

        return index

    def make_bilingual_book(self):
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
        )
        new_book = self._make_new_book(self.origin_book)
        all_items = list(self.origin_book.get_items())
        trans_taglist = self.translate_tags.split(",")
        all_p_length = sum(
            0
            if (
                (i.get_type() != ITEM_DOCUMENT)
                or (i.file_name in self.exclude_filelist.split(","))
                or (
                    self.only_filelist
                    and i.file_name not in self.only_filelist.split(",")
                )
            )
            else len(bs(i.content, "html.parser").findAll(trans_taglist))
            for i in all_items
        )
        all_p_length += self.allow_navigable_strings * sum(
            0
            if (
                (i.get_type() != ITEM_DOCUMENT)
                or (i.file_name in self.exclude_filelist.split(","))
                or (
                    self.only_filelist
                    and i.file_name not in self.only_filelist.split(",")
                )
            )
            else len(bs(i.content, "html.parser").findAll(text=True))
            for i in all_items
        )
        pbar = tqdm(total=self.test_num) if self.is_test else tqdm(total=all_p_length)
        print()
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            if self.retranslate:
                self.retranslate_book(
                    index, p_to_save_len, pbar, trans_taglist, self.retranslate
                )
                exit(0)
            
            # 加入非文本 item
            # Add the things that don't need to be translated first, so that you can see the img after the interruption
            for item in self.origin_book.get_items():
                if item.get_type() != ITEM_DOCUMENT:
                    new_book.add_item(item)

            # 到这里才真正开始翻译，遍历书本的 item, 翻译每个 item, ori item+ 翻译后的item 拼成新书
            # 在这里存储 item 为中间文件，使用 map reduce 进行翻译。
            # operator: map: item -> item, reduce: item + item -> new_book
            for item in self.origin_book.get_items_of_type(ITEM_DOCUMENT):
                index = self.process_item(
                    item, index, p_to_save_len, pbar, new_book, trans_taglist
                )

                if self.accumulated_num > 1:
                    name, _ = os.path.splitext(self.epub_name)
                    epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            name, _ = os.path.splitext(self.epub_name)
            epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            if self.accumulated_num == 1:
                pbar.close()
        except (KeyboardInterrupt, Exception) as e:
            print(e)
            if self.accumulated_num == 1:
                print("you can resume it next time")
                self._save_progress()
                self._save_temp_book()
            sys.exit(0)

    def load_state(self):
        try:
            with open(self.bin_path, "rb") as f:
                self.p_to_save = pickle.load(f)
        except Exception:
            raise Exception("can not load resume file")

    def _save_temp_book(self):
        # TODO refactor this logic
        origin_book_temp = epub.read_epub(self.epub_name)
        new_temp_book = self._make_new_book(origin_book_temp)
        p_to_save_len = len(self.p_to_save)
        trans_taglist = self.translate_tags.split(",")
        index = 0
        try:
            for item in origin_book_temp.get_items():
                if item.get_type() == ITEM_DOCUMENT:
                    soup = bs(item.content, "html.parser")
                    p_list = soup.findAll(trans_taglist)
                    if self.allow_navigable_strings:
                        p_list.extend(soup.findAll(text=True))
                    for p in p_list:
                        if not p.text or self._is_special_text(p.text):
                            continue
                        # TODO banch of p to translate then combine
                        # PR welcome here
                        if index < p_to_save_len:
                            new_p = copy(p)
                            if type(p) == NavigableString:
                                new_p = self.p_to_save[index]
                            else:
                                new_p.string = self.p_to_save[index]
                            self.helper.insert_trans(
                                p,
                                new_p.string,
                                self.translation_style,
                                self.is_bilingual,
                            )
                            index += 1
                        else:
                            break
                    # for save temp book
                    if soup:
                        item.content = soup.encode()
                new_temp_book.add_item(item)
            name, _ = os.path.splitext(self.epub_name)
            epub.write_epub(f"{name}_bilingual_temp.epub", new_temp_book, {})
        except Exception as e:
            # TODO handle it
            print(e)

    def _save_progress(self):
        try:
            with open(self.bin_path, "wb") as f:
                pickle.dump(self.p_to_save, f)
        except Exception:
            raise Exception("can not save resume file")

# 结构体：paragraph, item, document
class MParagraph():
    def __init__(self, para_id: str, p:Tag=None, token_num: int=0, isTranslatable=False, transed_text=""):
        self.para_id = para_id # 格式: documentName_itemID_paragraphID
        self.p = p
        self.token_num = token_num
        self.isTranslatable = isTranslatable
        self.transed_text=transed_text
        self.oriText = ""

    def __str__(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_dict(self):
        return {
            "para_id ":self.para_id ,
            "p_text ":self.p.get_text() ,
            "token_num ":self.token_num ,
            "is_translatable ":self.isTranslatable ,
            "transed_text":self.transed_text,
            "ori_text ":self.oriText ,
        }


class PackedTxtToTrans():
    def __init__(self, document_name:str, packID: str, oriText:str, transed_text:str="", prompt:str="", isTranslated:bool=False):
        self.document_name = document_name
        self.packID = packID
        self.oriText = oriText
        self.transed_text = transed_text
        self.prompt = prompt
        self.isTranslated = isTranslated

    def to_dict(self):
        return {
            "document_name": self.document_name,
            "pack_id": self.packID,
            "ori_text": self.oriText,
            "transed_text": self.transed_text,
            "prompt": self.prompt,
            "is_translated": self.isTranslated
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            document_name = data["document_name"],
            packID = data["pack_id"], 
            oriText = data["ori_text"], 
            transed_text = data["transed_text"], 
            prompt = data["prompt"], 
            isTranslated = data["is_translated"]
        )

class MEpubDocItem():
    def __init__(self, document_name: str, oriItem: epub.EpubItem, mpara_list:list[MParagraph], soup:bs) -> None:
        self.document_name = document_name
        self.tot_token_num = 0
        self.mpara_list = mpara_list
        self.ori_item = oriItem
        self.soup = soup

        # self.oriItem = oriItem
    def __str__(self):
        res = f"document_name: {self.document_name}, " \
            f"item_id/filename: {self.ori_item.id} / {self.ori_item.file_name}, " \
            f"total token num: {self.tot_token_num}, " \
            f"content: {self.ori_item.content[:10]}"  

        if len(self.mpara_list) > 0: 
            res += f"\nFirst one paragraph in html: {self.mpara_list[0]}" # 这里 不懂为啥会将 PID 的末尾的段落索引变成0
            # res += f"\nFirst one paragraph in text: {self.mpara_list[0].p.text[:50]}" 
        return res


class DB:
    def __init__(self) -> None:
        self.hash = {}
        pass

    def get(self, key):
        if key in self.hash:
            return self.hash[key]
        else:
            return None
    
    def set(self, key, value):
        pass

class Options():
    def __init__(self) -> None:
        self.mode="test"
        pass

class FakeModel():
    def __init__(self, prompt: str):
        self.prompt = prompt

    def counter(self, x:str)->int:
        return 0

    def getPromptLen(self) -> int:
        return 0
    
    def getPromptStr(self) -> str:
        return self.prompt

class DeeplModel(FakeModel):
    def __init__(self, prompt: str):
        super().__init__(prompt)

    def counter(self, x:str) -> int:
        return len(x)

    def getPromptLen(self) -> int:
        res = 0
        res +=  self.counter(self.prompt) 
        res += self.counter('<title para_id="1632873462000000000" class="context"> </title>')
        res += 50
        return res
    
class ChatGPTAPIModel(FakeModel):
    def __init__(self, prompt: str):
        super().__init__(prompt)

    def counter(self, x:str) -> int:
        return num_tokens_from_text(x)

    def getPromptLen(self) -> int: 
        promptstr = """const HTML_TRANSLATOR_USER_PROMPT_TEMPLATE = `
        Please help me to translate the following text in \
        %s format surrounded by triple quote ``` from %s to %s. %s \
        You have to obey rules:
        1. Keep the html format and translate the content in the tag.
        2. Remember only return translated text in html format, no original text, no explanation such as "Here is the translated text blablabla"

        ```
        %s
        ```
        """
        return self.counter(self.prompt) + self.counter(promptstr) + 50

class EPUBBookLoaderV2:

    """
    op1: epub -> list[docItem]
    op2.1: filter paragraph
    op2.2: item -> list[paragraph]
    op3: list[paragraph] -> list[packedText]
    op4: list[packedText] -> list[packedText]
    op5: list[packedText] -> dict{"pid"->paragraph}
    op6: get list[paragraph] from op1 and op2, then merge paragraph with paragraph from op5 by pid
    """

    def __init__(
        self,
        filepath : str,
        model: FakeModel,
        exclude_translate_tags : str = "sup",
        allow_navigable_strings : bool = False,
        translation_style : str = "",
        is_bilingual : bool = True,
        translate_tags : str = "p",
        counter : Callable = None,
        rawprompt: str = ""
    ):
        self.filepath = filepath 
        self.translate_tags = translate_tags 
        self.exclude_translate_tags = exclude_translate_tags 
        self.allow_navigable_strings = allow_navigable_strings 
        self.translation_style = translation_style 
        self.is_bilingual = is_bilingual 
        self.document_name = os.path.splitext(os.path.basename(filepath))[0]
        self.new_epub = epub.EpubBook()
        self.counter = (lambda x: len(x)) if counter is None else counter
        self.model = model
            
        # monkey patch for # 173: fixed missing html tag <head>
        def _write_items_patch(obj):
            for item in obj.book.get_items():
                if isinstance(item, epub.EpubNcx):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), obj._get_ncx()
                    )
                elif isinstance(item, epub.EpubNav):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name),
                        obj._get_nav(item),
                    )
                elif item.manifest:
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), item.content
                    )
                else:
                    obj.out.writestr("%s" % item.file_name, item.content)

        epub.EpubWriter._write_items = _write_items_patch
        try:
            self.origin_book = epub.read_epub(self.filepath)
        except Exception:
            # tricky monkey patch for #71 if you don't know why please check the issue and ignore this
            # when upstream change will TODO fix this
            def _load_spine(obj):
                spine = obj.container.find("{%s}%s" % (epub.NAMESPACES["OPF"], "spine"))

                obj.book.spine = [
                    (t.get("idref"), t.get("linear", "yes")) for t in spine
                ]
                obj.book.set_direction(spine.get("page-progression-direction", None))

            epub.EpubReader._load_spine = _load_spine
            self.origin_book = epub.read_epub(self.filepath)

    @staticmethod
    def _is_special_text(text):
        return (
            text.isdigit()
            or text.isspace()
            or is_text_link(text)
            or all(char in string.punctuation for char in text)
        )

# paragraph
    def has_nest_child(self, element, trans_taglist):
        if isinstance(element, Tag):
            for child in element.children:
                if child.name in trans_taglist:
                    return True
                if self.has_nest_child(child, trans_taglist):
                    return True
        return False

    def filter_nest_list(self, p_list, trans_taglist):
        """
        过滤掉嵌套的 p , 即段落里还有个段落
        """
        filtered_list = [p for p in p_list if not self.has_nest_child(p, trans_taglist)]
        return filtered_list

    def excludeTag(self,p):
        """
        exclude tag

        Args:
            p: paragraph
        
        Returns:
            paragraph without tag in exclude_translate_tags
        """

        new_p = copy(p)
        for p_exclude in self.exclude_translate_tags.split(","):
            # for issue #280
            if type(p) == NavigableString:
                continue
            for pt in new_p.find_all(p_exclude):
                pt.extract()
        return new_p

    # mapper
    def statParagraph(self,p)->int:
        """
        count token of paragraph based on openai token counting rule

        Args:
            p (paragraph): paragraph to translate
        returns:
            int: token count

        """
        if not self.isTranslatable(p):
            return 0
        temp_p = copy(p)
        for p_exclude in self.exclude_translate_tags.split(","):
            # for issue #280
            if type(p) == NavigableString:
                continue
            for pt in temp_p.find_all(p_exclude):
                pt.extract()

        return num_tokens_from_text(temp_p.text)

    # reducer: 段落 pieces 组成一个大 block, 类似内存小块组成大块

    def isTranslatable(self,p) -> bool:
        if any (
            [not p.text, self._is_special_text(p.text), not_trans(p.text)]
        ):
            return False
        return True


# def splitDocItem(self, docItem:MEpubDocItem, size:int) -> list[MEpubDocItem]:
    # def readEpubItem(filename:str) -> list[tuple(str, epub.EpubItem)]:

    def parseEpub(self, filename:str) -> list[MEpubDocItem]:
        """
        op1: epub -> list[docItem]
        parse epub book, extract element in trans_taglist, exclude tags, filter nest list, stat token num

        Return
            list of MEpubDocItem
            NOTE: MEpubDocItem is reference
        """
        try:
            epubbook = epub.read_epub(filename)
        except Exception:
            # tricky monkey patch for #71 if you don't know why please check the issue and ignore this
            # whjen upstream change will TODO fix this
            def _load_spine(obj):
                spine = obj.container.find("{%s}%s" % (epub.NAMESPACES["OPF"], "spine"))

                obj.book.spine = [
                    (t.get("idref"), t.get("linear", "yes")) for t in spine
                ]
                obj.book.set_direction(spine.get("page-progression-direction", None))

            epub.EpubReader._load_spine = _load_spine
            epubbook = epub.read_epub(filename)

        res = []
        docitems = epubbook.get_items_of_type(ITEM_DOCUMENT)

        for docitem in docitems:
            soup = bs(docitem.content, "html.parser")
            trans_taglist = self.translate_tags.split(",")
            p_list = soup.findAll(trans_taglist)
            p_list = self.filter_nest_list(p_list, trans_taglist)

            if self.allow_navigable_strings:
                p_list.extend(soup.findAll(text=True))

            # p_list = copy(p_list)
            mParagraph_list = []
            for i in range(len(p_list)):
                para_id = f"{i}:{docitem.id}:{docitem.file_name}:{self.document_name}"
                mParagraph_list.append(
                    MParagraph(
                        para_id=para_id,
                        p=p_list[i],
                        isTranslatable=self.isTranslatable(p_list[i]),
                        # 翻译后替换掉
                    )
                )
            res.append(
                MEpubDocItem(
                    document_name=self.document_name,
                    oriItem=docitem,
                    mpara_list=mParagraph_list,
                    soup=soup
                )
            )
                # self.filterItem(copy(self.parseEpubItem(docitem, self.document_name)))
            
        return res

    
    def filterItem(self, item:MEpubDocItem) -> MEpubDocItem:
        """
        op2.1: filter item
        """
        if item is None:
            return item
        for i in range(len(item.mpara_list)):
            item.mpara_list[i].p = self.excludeTag(item.mpara_list[i].p)

        item.mpara_list = [p for p in item.mpara_list if p.isTranslatable]
        return item

    def mergeParas(self, items:list[MEpubDocItem]) -> list[MParagraph]:
        """
        op2.2: merge paragraph
        """
        res = []
        for item in items:
            res.extend(item.mpara_list)
        return res

# pack text
    def makePackedTextsFromParagraphs(self, p_list:list[MParagraph], pack_size: int) -> list[PackedTxtToTrans]:
        """
        op3: pack pieces of paragraph paras to large paragraph with at size of at most pack_size

        cnt > size: 
            1. j < len-1, append(p_list[i:j]), and forward; 
            2. j == len-1, append(p_list[i:j]), append([p_list[j]])
        cnt <= size: 
            1. j < len-1, forward; 
            2. j == len-1, append(p_list[i:])
        """
        if p_list is None or len(p_list) == 0:
            return []
        tmp_list = p_list

        res = []
        promptLength = self.model.getPromptLen()
        cnt = promptLength
        i = 0
        for j in range(len(tmp_list)):

            tmp_list[j].oriText = f'<p para_id="{tmp_list[j].para_id}">{tmp_list[j].p.get_text().strip()}</p>'
            tmp_list[j].token_num = self.counter(tmp_list[j].oriText)

            cnt += tmp_list[j].token_num

            # basically, there is no paragraph with length larger than 2k token, corresponding to 4k size
            # if there is ingnore it
            if cnt > pack_size and i == j:
                logger.warn(f"paragraph {tmp_list[i].para_id} is too large, please check it")
                i = j+1
                cnt=promptLength
                continue

            if cnt > pack_size:
                res.append(
                    PackedTxtToTrans(
                        document_name=self.document_name,
                        packID=f"{tmp_list[i].para_id},{tmp_list[j].para_id}",
                        oriText="".join([p.oriText for p in tmp_list[i:j]]),
                        prompt=self.model.getPromptStr()
                    )
                )
                i=j
                cnt=tmp_list[j].token_num+promptLength
                continue

            if j == len(tmp_list)-1:
                last_pack = tmp_list[i:] if cnt <= pack_size else [tmp_list[j]]
                last_pack_text = "".join([p.oriText for p in last_pack])
                packID = f"{tmp_list[i].para_id},{tmp_list[j].para_id}"  if cnt <= pack_size else f"{tmp_list[j].para_id},{tmp_list[j].para_id}"
                res.append(
                    PackedTxtToTrans(
                        document_name=self.document_name,
                        packID=packID, 
                        oriText=last_pack_text,
                        prompt=self.model.getPromptStr()
                    )
                )
        return res

    def getParaListFromPackedTextList(self, packedTextList: list[PackedTxtToTrans]) -> list[MParagraph]:
        """
        op: unpack packedTextList to list of MParagraph
        """
        res = []
        for packedText in packedTextList:
            soup = bs(packedText.oriText, "html.parser")
            for p in soup.findAll("p"):
                res.append(
                    MParagraph(
                        para_id=p["para_id"],
                        p=p,
                        isTranslatable=self.isTranslatable(p),
                    )
                )
        return res

    def pid2Paragraph(self) -> dict:
        """
        op5: deserialize packedText from a json file to list of MParagraph
        """
        res = {}
        for packedText in self.deserializePackedText():
            # logger.debug(packedText.transed_text)
            # logger.debug("\n")
            # sys.exit(0)
            soup = bs(packedText.transed_text, "html.parser")
            for p in soup.findAll("p"):
                res[p["para_id"]] = MParagraph(
                    para_id=p["para_id"],
                    transed_text=p.get_text(),
                )
        return res

    def serializePackedTextListToFile(self, packedTextList:list[PackedTxtToTrans]):
        """
        serialize big list of packedTextList to json file
        """
        # dir = os.path.dirname(pathname)
        # document_name = os.path.splitext(os.path.basename(filename))[0]
        if packedTextList is None or len(packedTextList) == 0:
            logger.warn("packedTextList is empty, please check it")
            return
        serialized_list = [pt.to_dict() for pt in packedTextList]
        with open(f"{self.filepath}.json", 'w') as f:
            json.dump(serialized_list, f, ensure_ascii=False, indent=None)
  
    def deserializePackedText(self) -> list[PackedTxtToTrans]:
        data_list = []
        with open(self.filepath+".json", 'r') as f:
            data_list = json.load(f)
        # 将每个字典转换为 PackedTxtToTrans 对象
        packedTextList = [PackedTxtToTrans.from_dict(data) for data in data_list]
        return packedTextList


    def _make_new_book(self, book):
        new_book = epub.EpubBook()
        new_book.metadata = book.metadata
        new_book.spine = book.spine
        new_book.toc = book.toc
        return new_book

    def make_bilingual_book(self):
        """
        这里解析 item 并生成 paragraph 需要与 parseEpubItem 保持一致
        """
        new_book = self._make_new_book(self.origin_book)

        for item in self.origin_book.get_items():
            if item.get_type() != ITEM_DOCUMENT:
                new_book.add_item(item)

        transedPDict = self.pid2Paragraph()
        for docitem in self.parseEpub(self.filepath):
            for i in range(len(docitem.mpara_list)):
                if docitem.mpara_list[i].para_id not in transedPDict:
                    continue
                self._insert_trans(docitem.mpara_list[i], transedPDict[docitem.mpara_list[i].para_id])
                # logger.debug(docitem.mpara_list[i].p.get_text())
                # sys.exit(0)
            if docitem.soup:
                docitem.ori_item.content = docitem.soup.encode()
            # logger.debug(docitem.ori_item.content)
            new_book.add_item(docitem.ori_item)
        name, _ = os.path.splitext(self.filepath)
        epub.write_epub(f"{name}_bilingual.epub", new_book, {})


    def _insert_trans(self, oriP:MParagraph, transedP:MParagraph):

        """
        merge paragraph with mp, from insert_trans
        """
        if (
            oriP.p and oriP.p.string is not None
            and oriP.p.string.replace(" ", "").strip() == transedP.transed_text.replace(" ", "").strip()
        ):
            return
        new_p = copy(oriP.p)
        new_p.string = transedP.transed_text
        if self.translation_style != "":
            new_p["style"] = self.translation_style
        oriP.p.insert_after(new_p)
        if not self.is_bilingual:
            # 删掉原文
            oriP.p.extract()

def main():
    pass