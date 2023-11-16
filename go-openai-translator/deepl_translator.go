package main

import (
	"fmt"
	"os"
	"regexp"
	"sync"

	"bytes"
	"encoding/json"
	"errors"
	"io"
	"log"
	"math/rand"
	"net/http"
	urllib "net/url"
	"strings"
	"time"

	"github.com/abadojack/whatlanggo"
	"github.com/andybalholm/brotli"
	mapreduce "github.com/kevwan/mapreduce/v2"
	"github.com/tidwall/gjson"
	"golang.org/x/net/html"
)

type DeeplTranslator struct {
	maxCtxLength   int
	maxAttempRetry int
	proxyURL       string
}

type Lang struct {
	SourceLangUserSelected string `json:"source_lang_user_selected"`
	TargetLang             string `json:"target_lang"`
}

type CommonJobParams struct {
	WasSpoken    bool   `json:"wasSpoken"`
	TranscribeAS string `json:"transcribe_as"`
	// RegionalVariant string `json:"regionalVariant"`
}

type Params struct {
	Texts           []Text          `json:"texts"`
	Splitting       string          `json:"splitting"`
	Lang            Lang            `json:"lang"`
	Timestamp       int64           `json:"timestamp"`
	CommonJobParams CommonJobParams `json:"commonJobParams"`
}

type Text struct {
	Text                string `json:"text"`
	RequestAlternatives int    `json:"requestAlternatives"`
}

type PostData struct {
	Jsonrpc string `json:"jsonrpc"`
	Method  string `json:"method"`
	ID      int64  `json:"id"`
	Params  Params `json:"params"`
}

type ResData struct {
	TransText  string `json:"text"`
	SourceLang string `json:"source_lang"`
	TargetLang string `json:"target_lang"`
}

func initData(sourceLang string, targetLang string) *PostData {
	return &PostData{
		Jsonrpc: "2.0",
		Method:  "LMT_handle_texts",
		Params: Params{
			Splitting: "newlines",
			Lang: Lang{
				SourceLangUserSelected: sourceLang,
				TargetLang:             targetLang,
			},
			CommonJobParams: CommonJobParams{
				WasSpoken:    false,
				TranscribeAS: "",
				// RegionalVariant: "en-US",
			},
		},
	}
}

func getICount(translateText string) int64 {
	return int64(strings.Count(translateText, "i"))
}

func getRandomNumber() int64 {
	r := rand.New(rand.NewSource(time.Now().UnixNano()))
	num := r.Int63n(99999) + 8300000
	return num * 1000
}

func getTimeStamp(iCount int64) int64 {
	ts := time.Now().UnixMilli()
	if iCount != 0 {
		iCount = iCount + 1
		return ts - ts%iCount + iCount
	} else {
		return ts
	}
}

func MakeDeeplTranslator(maxCtxLength int, maxAttempRetry int, proxyURL string) *DeeplTranslator {
	return &DeeplTranslator{
		maxCtxLength:   maxCtxLength,
		maxAttempRetry: maxAttempRetry,
		proxyURL:       proxyURL,
	}
}

func (t *DeeplTranslator) GenRandomHeader() {
	header := http.Header{}
	header.Set("Content-Type", "application/json")
	header.Set("Accept", "*/*")
	header.Set("x-app-os-name", "iOS")
	header.Set("x-app-os-version", "16.3.0")
	header.Set("Accept-Language", "en-US,en;q=0.9")
	header.Set("Accept-Encoding", "gzip, deflate, br")
	header.Set("x-app-device", "iPhone13,2")
	header.Set("User-Agent", "DeepL-iOS/2.9.1 iOS 16.3.0 (iPhone13,2)")
	header.Set("x-app-build", "510265")
	header.Set("x-app-version", "2.9.1")
	header.Set("Connection", "keep-alive")
}

func (t *DeeplTranslator) Translate(
	translateText string, sourceLang string, targetLang string, numberAlternative int,
) (interface{}, error) {

	id := getRandomNumber()
	if sourceLang == "" {
		lang := whatlanggo.DetectLang(translateText)
		deepLLang := strings.ToUpper(lang.Iso6391())
		sourceLang = deepLLang
	}
	if targetLang == "" {
		targetLang = "EN"
	}

	if translateText == "" {
		return map[string]interface{}{
			"message": "No Translate Text Found",
		}, errors.New("no translate text found")
	} else {
		url := "https://www2.deepl.com/jsonrpc"
		id = id + 1
		postData := initData(sourceLang, targetLang)
		text := Text{
			Text:                translateText,
			RequestAlternatives: numberAlternative,
		}
		postData.ID = id
		postData.Params.Texts = append(postData.Params.Texts, text)
		postData.Params.Timestamp = getTimeStamp(getICount(translateText))
		post_byte, _ := json.Marshal(postData)
		postStr := string(post_byte)

		if (id+5)%29 == 0 || (id+3)%13 == 0 {
			postStr = strings.Replace(postStr, "\"method\":\"", "\"method\" : \"", -1)
		} else {
			postStr = strings.Replace(postStr, "\"method\":\"", "\"method\": \"", -1)
		}
		// Logger.Debugf("postStr: %s", postStr)

		post_byte = []byte(postStr)
		reader := bytes.NewReader(post_byte)
		request, err := http.NewRequest("POST", url, reader)
		if err != nil {
			log.Println(err)
			return nil, err
		}

		// Set Headers
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Accept", "*/*")
		request.Header.Set("x-app-os-name", "iOS")
		request.Header.Set("x-app-os-version", "16.3.0")
		request.Header.Set("Accept-Language", "en-US,en;q=0.9")
		request.Header.Set("Accept-Encoding", "gzip, deflate, br")
		request.Header.Set("x-app-device", "iPhone13,2")
		request.Header.Set("User-Agent", "DeepL-iOS/2.9.1 iOS 16.3.0 (iPhone13,2)")
		request.Header.Set("x-app-build", "510265")
		request.Header.Set("x-app-version", "2.9.1")
		request.Header.Set("Connection", "keep-alive")

		client := &http.Client{}

		if t.proxyURL != "" {
			// Logger.Debugf("proxy url: %s", t.proxyURL)
			tmpURL, err := urllib.Parse(t.proxyURL)
			if err != nil {
				panic(err)
			}
			client.Transport = &http.Transport{
				Proxy: http.ProxyURL(tmpURL),
			}
			client.Timeout = 5 * time.Second
		}

		resp, err := client.Do(request)
		if err != nil {
			log.Println(err)
			return nil, err
		}
		defer resp.Body.Close()

		var bodyReader io.Reader
		switch resp.Header.Get("Content-Encoding") {
		case "br":
			bodyReader = brotli.NewReader(resp.Body)
		default:
			bodyReader = resp.Body
		}

		body, err := io.ReadAll(bodyReader)
		if err != nil {
			log.Println(err)
			return nil, err
		}

		res := gjson.ParseBytes(body)
		// Logger.Debug(res)

		if res.Get("error.code").String() == "-32600" {
			log.Println(res.Get("error").String())
			return nil, errors.New("Invalid targetLang")
		}

		if resp.StatusCode == http.StatusTooManyRequests {

			return nil, errors.New("Too Many Requests")
		} else {
			var alternatives []string
			res.Get("result.texts.0.alternatives").ForEach(func(key, value gjson.Result) bool {
				alternatives = append(alternatives, value.Get("text").String())
				return true
			})
			return map[string]interface{}{
				"id":           id,
				"data":         res.Get("result.texts.0.text").String(),
				"alternatives": alternatives,
			}, nil
		}
	}
}

func (t *DeeplTranslator) TranslateHtmlText(
	packedText PackedTxtToTrans, srcLang string, targetLang string, model string,
) (PackedTxtToTrans, error) {

	addPrompt := func(packedText PackedTxtToTrans) string {
		return fmt.Sprintf(
			`<title para_id="%d" class="prompt"> %s </title> %s`,
			time.Now().UnixNano(),
			packedText.Prompt,
			packedText.OriText,
		)
	}

	filterPrompt := func(text string) string {
		re := regexp.MustCompile(`^<title para_id="[0-9]+" class="prompt">[^<]+</title>`)
		return re.ReplaceAllString(text, "")
	}

	// Add Prompt
	// Here prompt should not be added to packedText.OriText， because
	// translation retrial will repeatedly add prompt to text
	tmpText := addPrompt(packedText)

	if len(tmpText) > t.maxCtxLength {
		Logger.Errorf("text is too large, please split it, pack_id=%s, length of text: %d, limit:%d", packedText.PackID, len(packedText.OriText), t.maxCtxLength)
		return packedText, errors.New("too long text")
	}

	for attemptCount := 0; attemptCount < t.maxAttempRetry; attemptCount++ {
		res, err := t.Translate(tmpText, srcLang, targetLang, 0)
		if err != nil {
			Logger.Warnf("deepl translation failed pid:%s err:%v\n", packedText.PackID, err)
			continue
		}
		tmpres, _ := res.(map[string]interface{})
		data, _ := tmpres["data"].(string)
		data, err = UnescapeUnicode(data)
		data = filterPrompt(data)
		if err != nil {
			Logger.Errorf("utf8 unescape failed, pid: %s, err: %v", packedText.PackID, err)
			continue
		}
		packedText.TransedText = data
		packedText.IsTranslated = true
		return packedText, nil
	}

	Logger.Warnf("Get %d consecutive errors", t.maxAttempRetry)
	return packedText, fmt.Errorf(
		"packed text with PID: %s get %d consecutive exceptions", packedText.PackID, t.maxAttempRetry,
	)
}

func (t *DeeplTranslator) TranslationChecker(packedText PackedTxtToTrans) bool {
	getParaIDs := func(s string) (map[string]bool, error) {
		doc, err := html.Parse(strings.NewReader(s))
		if err != nil {
			return nil, err
		}
		paraIDs := make(map[string]bool)
		var f func(*html.Node)
		f = func(n *html.Node) {
			if n.Type == html.ElementNode && n.Data == "p" {
				for _, a := range n.Attr {
					if a.Key == "para_id" {
						paraIDs[a.Val] = true
					}
				}
			}
			for c := n.FirstChild; c != nil; c = c.NextSibling {
				f(c)
			}
		}
		f(doc)
		return paraIDs, nil
	}

	originalParaIDs, err := getParaIDs(packedText.OriText)
	if err != nil {
		return false
	}
	translatedParaIDs, err := getParaIDs(packedText.TransedText)
	if err != nil {
		return false
	}

	if len(originalParaIDs) != len(translatedParaIDs) {
		return false
	}
	for paraID := range originalParaIDs {
		if !translatedParaIDs[paraID] {
			return false
		}
	}
	return true
}

// wrap TranslateHtmlText to a mapper and update state
func (t *DeeplTranslator) MakeHtmlTranslationMapper() func(
	PackedTxtToTrans, mapreduce.Writer[PackedTxtToTrans],
	func(error),
) {
	return func(packedText PackedTxtToTrans, writer mapreduce.Writer[PackedTxtToTrans], cancel func(error)) {
		if !packedText.IsTranslated {
			packedText, err := t.TranslateHtmlText(packedText, DPL_LANG_ENGLISH, DPL_LANG_CHINESE_SIMPLIFIED, "")
			// 这里 已经写了，后面再 给 istranslated = true 已经不管用了。
			writer.Write(packedText)

			if err != nil {
				Logger.Errorf("translation of packed text with PID: %s failed: %v", packedText.PackID, err)
				state.AddFailedPackID(packedText.DocumentName, packedText.PackID)
				return
			}
			if !t.TranslationChecker(packedText) {
				packedText.IsTranslated = false
				Logger.Errorf("translation of packed text with PID: %s failed: %v", packedText.PackID, err)
				state.AddFailedPackID(packedText.DocumentName, packedText.PackID)
				return
			}
			state.progressBar.Increment()
			state.AddTransedPackedText(packedText.DocumentName, packedText)
		}
	}
}

// 大概是 5000， 超过了会报 too many requests
func (t *DeeplTranslator) LimitDetector() int {

	makeTextOfLength := func(length int) string {
		var text string
		for len(text)+13 < length {
			text = fmt.Sprintf("%s%s", text, "hello world. ")
		}
		return text
	}
	proxyURL := os.Getenv("PROXY_URL")
	length := 4000
	wg := sync.WaitGroup{}
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func(length int) {
			translator := MakeDeeplTranslator(length, 3, proxyURL)
			text := makeTextOfLength(length)
			res, err := translator.Translate(text, "EN", "ZH", 0)
			if err != nil {
				fmt.Printf("length: %d, error: %v\n", length, err)
			} else {
				tmpres, _ := res.(map[string]interface{})
				data, _ := tmpres["data"].(string)
				if len(data) > 100 {
					data = data[0:100]
				}
				fmt.Printf("text with length: %d, succ, res: %s\n", len(text), data)
			}
			wg.Done()
		}(length)
	}
	wg.Wait()
	return length
}

// type TestDeeplTranslator struct {
// }
