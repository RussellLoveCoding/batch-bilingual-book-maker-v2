package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"testing"

	mapreduce "github.com/kevwan/mapreduce/v2"
	openai "github.com/sashabaranov/go-openai"
)

func TestTranslatorPromptEditor(t *testing.T) {
	// 创建一个 TranslatorPromptEditor 实例
	editor := MakeTranslatorPromptEditor("html", "English", "Chinese")
	// 测试 fillTextGetUserPrompt 方法
	text := "Hello, world!"
	result := editor.fillTextGetUserPrompt(text, "This is a test.")
	fmt.Println(result)
}

func TestEpubLoader(t *testing.T) {
	e := EpubLoader{}
	packedTexts := e.ReadPackedTextList(filename)
	fmt.Printf("length of ps: %d\n", len(packedTexts))
	for _, p := range packedTexts {
		fmt.Println("--------------------------------")
		fmt.Printf("pid: %s, context: %s, former stirng: %s \n", p.PackID, p.Prompt, p.OriText[:50])
	}
}

func TestGPTTranslator(t *testing.T) {
	// fmt.Println(result)
	// 测试 fillTextGetUserPrompt 方法
	// loggerInit()
	text := "<p>Hello, world!</p>"
	packedTxt := PackedTxtToTrans{
		PackID:      "testID",
		OriText:     text,
		Prompt:      "",
		TransedText: "",
	}

	key := ""
	proxyURL := "http://127.0.0.1:7891"
	// proxyURL := ""
	baseURL := "https://ai.fakeopen.com/v1"
	gpttranslator := MakeGptTranslator(key, proxyURL, baseURL, 8000)

	packecdText, err := gpttranslator.TranslateHtmlText(packedTxt, "English", "Chinese", openai.GPT3Dot5Turbo)
	// 需要记录该 packedText
	if err != nil {
		t.Errorf(
			`text translation failed for packed text with pid: %s and content: %s, which would be retried later, error: %v`,
			packedTxt.PackID,
			packedTxt.OriText,
			err,
		)
	}
	fmt.Println(packecdText.TransedText)

	fmt.Println("--------------------- state --------------------")
	jsonstr, _ := json.Marshal(state)
	fmt.Println(string(jsonstr))
}

func TestMapReduceTask(t *testing.T) {
	loader := EpubLoader{}
	proxyURL := "http://127.0.0.1:7891"
	// proxyURL := ""
	baseURL := "https://ai.fakeopen.com/v1"
	translator := MakeGptTranslator(key, proxyURL, baseURL, 8000)
	res, err := mapreduce.MapReduce(
		loader.MakePackedListGenerator(filename),
		translator.MakeHtmlTranslationMapper(),
		loader.MakeSavePListReducer(filename),
		mapreduce.WithWorkers(1),
	)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(res)
}

func TestBaseTranslator(t *testing.T) {
	proxyURL := os.Getenv("PROXY_URL")
	deeplTranslator := MakeDeeplTranslator(8000, 3, proxyURL)
	result, err := deeplTranslator.Translate("Hello World!", "EN", "ZH", 0)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
	} else {
		tmpres, _ := result.(map[string]interface{})
		data, _ := tmpres["data"].(string)
		fmt.Println(data)
	}
}

func TestDeeplTranslatorLimit(t *testing.T) {
	proxyURL := os.Getenv("PROXY_URL")
	deeplTranslator := MakeDeeplTranslator(10000, 3, proxyURL)
	limit := deeplTranslator.LimitDetector()
	fmt.Println("max limit: ", limit)
}

func TestDeeplTranslatorMRTask(t *testing.T) {
	deeplmaster(30)
}

func TestUnicodeEncoding(t *testing.T) {
	DB := MakeRedisDB()
	str, _ := DB.HGet("foo_transed_text_list", "0:ch001_xhtml:text/ch001.xhtml:foo,10:ch001_xhtml:text/ch001.xhtml:foo")
	str, _ = UnescapeUnicode(str)
	fmt.Println(str)
}
