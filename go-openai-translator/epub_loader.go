package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/cheggaaa/pb/v3"
	mapreduce "github.com/kevwan/mapreduce/v2"
)

type EpubLoader struct {
}

//	ReadPackedTextList reads a packed text list from the specified file.
//
// Arguments:
// - filename: the file name of the packed text list
// Returns:
// - packedTextList: a list of PackedTxtToTrans
func (e *EpubLoader) ReadPackedTextList(filename string) []PackedTxtToTrans {
	file, err := os.Open(filename + ".json")
	if err != nil {
		Logger.Errorf("checkpoint loading from file %s failed: %v", filename, err)
		panic(err)
	}
	defer file.Close()

	byteValue, _ := io.ReadAll(file)
	var packedTextList []PackedTxtToTrans
	err = json.Unmarshal(byteValue, &packedTextList)
	if err != nil {
		Logger.Errorf("checkpoint loading from file %s failed: %v", filename, err)
		panic(err)
	}
	return packedTextList
}

// read file and return a generator
// TODO: (a) when runningUntilFinishFlag is true: feed out channel with both plist
// from file and failed-to-be-translated plist; (b) Otherwise, feed out channel with
// only p list read from file.

// wrap ReadPackedTextList to a generator and add document to state
func (e *EpubLoader) MakePackedListGenerator(filename string) func(out chan<- PackedTxtToTrans) {
	packedTextList := e.ReadPackedTextList(filename)
	state.progressBar = pb.StartNew(len(packedTextList))
	documentName := filepath.Base(filename)
	state.AddDocument(documentName, packedTextList)
	return func(out chan<- PackedTxtToTrans) {
		for _, p := range packedTextList {
			out <- p
		}
	}
}

// save the translated text to json file, and return statistics
func (e *EpubLoader) savePackedList(filename string, packedTextList []PackedTxtToTrans) (string, error) {

	transedCnt, failCnt := 0, 0
	for _, p := range packedTextList {
		if p.IsTranslated {
			transedCnt++
		} else {
			failCnt++
		}
	}
	data, _ := json.Marshal(packedTextList)
	err := os.WriteFile(filename+".json", data, 0644)
	if err != nil {
		Logger.Errorf("translated packed text list saving to file %s failed: %v", filename, err)
		return "", err
	}

	return fmt.Sprintf(`----------------------------------------------
translated_count: %d, failed_count: %d`, transedCnt, failCnt), nil

}

// wrap savePackedList to a reducer
func (e *EpubLoader) MakeSavePListReducer(filename string) func(<-chan PackedTxtToTrans, mapreduce.Writer[string], func(error)) {
	return func(pipe <-chan PackedTxtToTrans, writer mapreduce.Writer[string], cancel func(error)) {
		var packedTextList []PackedTxtToTrans
		for p := range pipe {
			packedTextList = append(packedTextList, p)
		}
		statRes, err := e.savePackedList(filename, packedTextList)
		if err != nil {
			cancel(err)
		}
		writer.Write(statRes)
	}
}

