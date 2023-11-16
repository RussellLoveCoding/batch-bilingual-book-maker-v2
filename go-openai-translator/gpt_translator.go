package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"time"

	mapreduce "github.com/kevwan/mapreduce/v2"
	"github.com/sashabaranov/go-openai"
)

type GptTranslator struct {
	client         *openai.Client
	maxCtxLength   int
	maxAttempRetry int
}

// PromptEditor //

func MakeTranslatorPromptEditor(format string, src_lang string, target_lang string) *TranslatorPromptEditor {
	t := &TranslatorPromptEditor{
		format:      format,
		src_lang:    src_lang,
		target_lang: target_lang,
	}
	t.user_content = HTML_TRANSLATOR_USER_PROMPT_TEMPLATE
	t.sys_content = "You are a professional translator."
	return t
}

func (t *TranslatorPromptEditor) setFormat(format string) *TranslatorPromptEditor {
	t.format = format
	return t
}

func (t *TranslatorPromptEditor) setLangPair(src_lang string, target_lang string) *TranslatorPromptEditor {
	t.src_lang = src_lang
	t.target_lang = target_lang
	return t
}

func (t *TranslatorPromptEditor) fillTextGetUserPrompt(text string, context string) string {
	/*
	   const HTML_TRANSLATOR_USER_PROMPT_TEMPLATE = `
	   Please help me to translate the following text in \
	   %s format surrounded by triple quote """ from %s to %s. %s \
	   You have to obey rules:
	   1. Keep the html format and translate the content in the tag.
	   2. Remember only return translated text in html format, no original text, no explanation such as "Here is the translated text blablabla"

	   """
	   %s
	   """
	   `
	*/
	return fmt.Sprintf(t.user_content, t.format, t.src_lang, t.target_lang, context, text)
}

func (t *TranslatorPromptEditor) getSysMsg() string {
	return t.sys_content
}

func (t *TranslatorPromptEditor) getUserMsg() string {
	return t.user_content
}

// GptTranslator //

// Make a gpt translator
func MakeGptTranslator(key, proxyURL, baseURL string, modelCtxLength int) *GptTranslator {

	if key == "" {
		fmt.Println("Please set your openai key in environment variable OPENAI_API_KEY")
		Logger.Errorf("no api key provided")
		os.Exit(0)
	}
	if baseURL == "" {
		baseURL = "https://api.openai.com/v1"
	}
	if modelCtxLength == 0 {
		modelCtxLength = 8000 // 默认调用 ai.fakeopen.com 的是8K模型
	}

	config := openai.DefaultConfig(key)
	Logger.Debug("Key", key)
	config.BaseURL = baseURL
	config.APIType = openai.APITypeOpenAI
	config.OrgID = ""
	// 最多等三秒钟
	config.HTTPClient = &http.Client{
		Timeout: 5 * time.Second,
	}
	config.EmptyMessagesLimit = 300

	if proxyURL != "" {
		tmpURL, err := url.Parse(proxyURL)
		if err != nil {
			panic(err)
		}
		transport := &http.Transport{
			Proxy: http.ProxyURL(tmpURL),
		}

		config.HTTPClient = &http.Client{
			Transport: transport,
		}
	}

	return &GptTranslator{
		client:         openai.NewClientWithConfig(config),
		maxCtxLength:   modelCtxLength,
		maxAttempRetry: 1,
	}
}

func (t *GptTranslator) TranslateHtmlText(
	packedText PackedTxtToTrans, srcLang string, targetLang string, model string,
) (PackedTxtToTrans, error) {

	if len(packedText.OriText) > t.maxCtxLength/2-200 {
		Logger.Errorf("text is too large, please split it")
		return packedText, errors.New("too long text")
	}

	promptEditor := MakeTranslatorPromptEditor("html", srcLang, targetLang)
	sysContent, userContent := promptEditor.getSysMsg(),
		promptEditor.fillTextGetUserPrompt(packedText.OriText, packedText.Prompt)

	for attemptCount := 0; attemptCount < t.maxAttempRetry; attemptCount++ {
		completion, err := t.client.CreateChatCompletion(
			context.Background(),
			openai.ChatCompletionRequest{
				Model: model,
				Messages: []openai.ChatCompletionMessage{
					{Role: openai.ChatMessageRoleUser, Content: userContent},
					{Role: openai.ChatMessageRoleSystem, Content: sysContent},
				},
			},
		)

		if err != nil {
			Logger.Warnf("CreateChatCompletion error: %v\n", err)
			continue
		}

		// Logger.Debugf("Completion message: %v", completion)
		finishReason := completion.Choices[0].FinishReason
		if finishReason != openai.FinishReasonStop {
			Logger.Warnf("pid=%s finish_reason=%s. Something went wrong.", packedText.PackID, finishReason)
			continue
		}

		packedText.TransedText = completion.Choices[0].Message.Content
		packedText.IsTranslated = true
		return packedText, nil

	}

	Logger.Warnf("Get %d consecutive errors", t.maxAttempRetry)

	return packedText, fmt.Errorf(
		"packed text with PID: %s get %d consecutive exceptions", packedText.PackID, t.maxAttempRetry,
	)
}

// wrap TranslateHtmlText to a mapper and update state
func (t *GptTranslator) MakeHtmlTranslationMapper() func(PackedTxtToTrans, mapreduce.Writer[PackedTxtToTrans], func(error)) {
	return func(packedText PackedTxtToTrans, writer mapreduce.Writer[PackedTxtToTrans], cancel func(error)) {
		if !packedText.IsTranslated {
			packedText, err := t.TranslateHtmlText(packedText, "English", "Chinese", openai.GPT3Dot5Turbo)
			writer.Write(packedText)
			if err != nil {
				Logger.Errorf("translation of packed text with PID: %s failed: %v", packedText.PackID, err)
				state.AddFailedPackID(packedText.DocumentName, packedText.PackID)
			} else {
				state.progressBar.Increment()
				state.AddTransedPackedText(packedText.DocumentName, packedText)
			}
		}
	}
}
