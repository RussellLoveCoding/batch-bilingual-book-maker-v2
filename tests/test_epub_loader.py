import unittest
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.epub_loader import EPUBBookLoaderV2
from book_maker.loader.epub_loader import FakeModel, DeeplModel, ChatGPTAPIModel
from book_maker.utils import logger
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
import os
# import g4f


class TestEPUBBookLoaderV2(unittest.TestCase):
    def test_make_bilingual_ebook(self):
        document_name = os.path.basename(filename).split(".")[0]
        document_description = " a political allegory where farm animals revolt against humans, only to witness the corruption of power as their leaders become just as oppressive."
        deeplmodel = DeeplModel(f"Some text extracted from Chapter in book '{document_name}', {document_description}")
        
        loader = EPUBBookLoaderV2(
            filepath=filename,
            model=deeplmodel
            )
        loader.make_bilingual_book()

    def test_parse_epub(self):
        document_name = os.path.basename(filename).split(".")[0]
        document_description = " a political allegory where farm animals revolt against humans, only to witness the corruption of power as their leaders become just as oppressive."
        deeplmodel = DeeplModel(f"Some text extracted from Chapter in book '{document_name}', {document_description}")
        def charCounter(x:str): return len(x)
        loader = EPUBBookLoaderV2(
            filepath=filename,
            counter=charCounter,
            model=deeplmodel
        )
        docitems = loader.parseEpub(filename)
        docitems = [loader.filterItem(item) for item in docitems]
        para_list = loader.mergeParas(docitems)

        blocksize = 4500
        packedList = loader.makePackedTextsFromParagraphs(para_list, blocksize)
        loader.serializePackedTextListToFile(packedList)
        newPackedList = loader.deserializePackedText()
        
        logger.debug(f"len(p_list of foo epub book) {len(para_list)}")
        logger.debug(f"length of packedList : {len(packedList)}" )
        logger.debug(f"length of packedList loading from json: {len(newPackedList)}" )
        for i in range(len(packedList)):
            assert(packedList[i].packID == newPackedList[i].packID)
            try:
                assert(len(packedList[i].oriText) < blocksize)
            except AssertionError:
                logger.error(f"Assertion failed: len(packedList[{i}].oriText) = {len(packedList[i].oriText)}, blocksize = {blocksize}")
                raise


        newParaList = loader.getParaListFromPackedTextList(newPackedList)
        assert(len(newParaList) == len(para_list))

class TestChatGPTAPITranslator(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        os.environ["http_proxy"] = "http://192.168.1.6:7891"
        os.environ["https_proxy"] = "http://192.168.1.6:7891"

    def test_translate(self):
        translator = ChatGPTAPI(
            # key="pk-this-is-a-real-free-pool-token-for-everyone", 
            language="none",
            api_base="https://ai.fakeopen.com/v1"
        )
        text = '<p pid="animal_farm_index_split_000.html_id119_0">Animal Farm: A Fairy Story</p>'
        result = translator.translate_html_text(
            textID="animal_farm_index_split_000.html_id119_0",
            text=text, src_lang="English", 
            target_lang="Chinese", 
            ctx="This text is exctracted from novel 'animal farm'."
        )
        print(result)
        # self.assertIsInstance(result, str)
        # self.assertNotEqual(result, text)

#     def test_g4f(self):
#         g4f.logging = True # enable logging
#         g4f.check_version = False # Disable automatic version checking
#         print(g4f.version) # check version
#         print(g4f.Provider.Ails.params)  # supported args

#         # Automatic selection of provider

#         # streamed completion
#         response = g4f.ChatCompletion.create(
#             model="gpt-3.5-turbo",
#             messages=[{"role": "user", "content": "Hello"}],
#             stream=True,
#         )

#         for message in response:
#             print(message, flush=True, end='')

#         # normal response
#         response = g4f.ChatCompletion.create(
#             model=g4f.models.gpt_4,
#             messages=[{"role": "user", "content": "Hello"}],
#         )  # alterative model setting

#         print(response)

    

    
if __name__ == '__main__':
    unittest.main()
