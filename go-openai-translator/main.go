package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"sync"
	"syscall"

	pb "github.com/cheggaaa/pb/v3"
	mapreduce "github.com/kevwan/mapreduce/v2"
)

// PromptEditor
const HTML_TRANSLATOR_USER_PROMPT_TEMPLATE = `
Please help me to translate the following text in %s format surrounded by
triple quote """ from %s to %s.  %s Please
1. Keep the html format and translate the content in the tag.
2. return result in json format with field "format" representing the translated text format,  
and field "translated_text"  representing the translated text keeping the original format.
Correct response would be like {"format": "html", "translated_text":"<p>你好世界</p>"}
3. important: Do not output any other text(such as explaination ) than the result json string.
4. important: Do not output any other text(such as explaination ) than the result json string.
5. important: Do not output any other text(such as explaination ) than the result json string.

"""
%s
"""
`

// DB 存一本书的数据结构
// documentName_succ_pids: {packedId1, packedId2, ...}
// documentName_transed_text_list: {packId1: "", packId2: "", ...}
// documentName_failed_pids: {packId1: "", packId2: "", ...}

type TranslatorState struct {
	DocumentList     []string `json:"document_list"`
	PackedTxtIDs     []string `json:"packed_text_ids"`
	CompletedPackIDs []string `json:"completed_pack_ids"`
	FailedPackIDs    []string `json:"failed_pack_ids"`
	progressBar      *pb.ProgressBar
	mu               sync.Mutex
	MDB              *DB
}

type TranslatorPromptEditor struct {
	format       string
	src_lang     string
	target_lang  string
	user_content string
	sys_content  string
}

type PackedTxtToTrans struct {
	PackID       string `json:"pack_id"`
	OriText      string `json:"ori_text"`
	Prompt       string `json:"prompt"`
	TransedText  string `json:"transed_text"`
	IsTranslated bool   `json:"is_translated"`
	DocumentName string `json:"document_name"`
}

type Loader interface {
	ReadPackedTextList(filename string) []PackedTxtToTrans
	MakePackedListGenerator(filename string) func(out chan<- PackedTxtToTrans)
	savePackedList(filename string, packedTextList []PackedTxtToTrans) (string, error)
	MakeSavePListReducer(filename string) func(<-chan PackedTxtToTrans, mapreduce.Writer[string], func(error))
}

type Translator interface {
	TranslateHtmlText(
		packedText PackedTxtToTrans, srcLang string, targetLang string, model string,
	) (PackedTxtToTrans, error)

	MakeHtmlTranslationMapper() func(
		PackedTxtToTrans, mapreduce.Writer[PackedTxtToTrans], func(error),
	)
}

var Logger *MLogger
var state *TranslatorState
var MDB *DB

const (
	TRANSLAION_STATUS_SUCC = true
	TRANSLAION_STATUS_FAIL = false
)

func init() {
	InitLog()
	MDB = MakeRedisDB()
	state = MakeTranslatorState([]string{}, []string{}, MDB)
}

// TranslatorState  //

func MakeTranslatorState(documentList []string, packedTextList []string, MDB *DB) *TranslatorState {
	state := &TranslatorState{
		DocumentList:     documentList,
		PackedTxtIDs:     packedTextList,
		CompletedPackIDs: []string{},
		FailedPackIDs:    []string{},
		mu:               sync.Mutex{},
		MDB:              MDB,
	}
	return state
}

func (s *TranslatorState) AddTransedPackedText(documentName string, packedText PackedTxtToTrans) {
	jsonstr, err := json.Marshal(packedText)
	if err != nil {
		Logger.Errorf("marshal packed text %v failed: %v", packedText, err)
		return
	}
	s.MDB.HSet(
		s.MDB.TransedTextListKeyOfDocument(documentName),
		packedText.PackID,
		string(jsonstr),
	)
	s.MDB.SAdd(s.MDB.SuccListKeyOfDocument(documentName), packedText.PackID)
}

func (s *TranslatorState) AddFailedPackID(documentName string, packID string) {
	s.MDB.SAdd(s.MDB.SuccListKeyOfDocument(documentName), packID)
}

func (s *TranslatorState) AddDocument(documentName string, packedTexts []PackedTxtToTrans) {
	for _, p := range packedTexts {
		s.MDB.HSet(s.MDB.TransedTextListKeyOfDocument(documentName), p.PackID, "")
	}
}

// state checkpointer
// func (s *TranslatorState) Checkpoint() {
// }

// EpubLoader //

// 崩溃保存
func crashSafe() {
	// if r := recover(); r != nil {
	// 	Logger.Errorf("panic: %v", r)
	// }
}

func deeplmaster(workercount int) {
	loader := &EpubLoader{}
	proxyURL := os.Getenv("PROXY_URL")
	translator := MakeDeeplTranslator(4500, 3, proxyURL)
	packedTextGenerator := loader.MakePackedListGenerator(filename)
	mapper := translator.MakeHtmlTranslationMapper()
	reducer := loader.MakeSavePListReducer(filename)
	res, err := mapreduce.MapReduce(
		packedTextGenerator,
		mapper,
		reducer,
		mapreduce.WithWorkers(workercount),
	)
	if err == nil {
		fmt.Println(res)
	} else {
		Logger.Errorf("mapreduce failed: %v", err)
	}
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(res)
}

// func gptworker() {
// 	loader := &EpubLoader{}
// 	apiKey := os.Getenv("API_KEY")
// 	proxyURL := os.Getenv("PROXY_URL")
// 	baseURL := "https://ai.fakeopen.com/v1"
// 	// apiKey = "pk-WB8mceWuA91ODy4V6Sj1rsgwk97eK0I6vPTwjS50fD0"
// 	if apiKey == "" {
// 		fmt.Println("API_KEY is not set")
// 		os.Exit(0)
// 	}
// 	translator := MakeGptTranslator(apiKey, proxyURL, baseURL, 7000)
// 	res, err := mrmaster(loader, translator, filename)
// 	if err == nil {
// 		fmt.Println(res)
// 	} else {
// 		Logger.Errorf("mapreduce failed: %v", err)
// 	}
// }

func main() {
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
}
