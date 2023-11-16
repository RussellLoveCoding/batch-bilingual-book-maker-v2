
package main
import (
	"strconv"
	"strings"
	"unicode"

	"github.com/BWbwchen/MapReduce/worker"
)
func Map(filename string, contents string, ctx worker.MrContext) {
	// function to detect word separators.
	ff := func(r rune) bool { return !unicode.IsLetter(r) }

	// split contents into an array of words.
	words := strings.FieldsFunc(contents, ff)

	for _, w := range words {
		ctx.EmitIntermediate(w, "1")
	}
}
func Reduce(key string, values []string, ctx worker.MrContext) {
	// return the number of occurrences of this word.
	ctx.Emit(key, strconv.Itoa(len(values)))
}

