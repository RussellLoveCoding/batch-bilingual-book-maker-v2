package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
	"github.com/sirupsen/logrus"
)

type MLogger struct {
	*logrus.Logger
}

type DB struct {
	*redis.Client
	ctx context.Context
}

func InitLog() {
	logger := logrus.New()
	logger.SetLevel(logrus.DebugLevel)
	logger.SetFormatter(&logrus.TextFormatter{ForceColors: true})
	loggerfile, err := os.OpenFile(
		os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0666,
	)
	if err != nil {
		panic(err)
	}
	logger.SetOutput(loggerfile)

	Logger = &MLogger{Logger: logger}
}

// MLogger //

func (l *MLogger) Error(args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	msg := fmt.Sprintf("ERROR inside %s:%d, err: %s", file, line, fmt.Sprint(args...))
	l.Logger.Error(msg)
}

func (l *MLogger) Errorf(format string, args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	format = fmt.Sprintf("ERROR %s:%d, %s", file, line, format)
	l.Logger.Errorf(format, args...)
}

func (l *MLogger) Warn(args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	msg := fmt.Sprintf("WARN inside %s:%d, err: %s", file, line, fmt.Sprint(args...))
	l.Logger.Warn(msg)
}

func (l *MLogger) Warnf(format string, args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	format = fmt.Sprintf("WARN %s:%d, %s", file, line, format)
	l.Logger.Warnf(format, args...)
}

func (l *MLogger) Debug(args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	msg := fmt.Sprintf("DEBUG inside %s:%d, err: %s", file, line, fmt.Sprint(args...))
	l.Logger.Debug(msg)
}

func (l *MLogger) Debugf(format string, args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	format = fmt.Sprintf("DEBUG %s:%d, %s", file, line, format)
	l.Logger.Debugf(format, args...)
}

func (l *MLogger) Info(args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	msg := fmt.Sprintf("INFO inside %s:%d, err: %s", file, line, fmt.Sprint(args...))
	l.Logger.Info(msg)
}

func (l *MLogger) Infof(format string, args ...interface{}) {
	_, file, line, _ := runtime.Caller(1)
	file = filepath.Base(file)
	format = fmt.Sprintf("INFO %s:%d, %s", file, line, format)
	l.Logger.Infof(format, args...)
}

// DB //
func MakeRedisDB() *DB {
	return &DB{
		Client: redis.NewClient(&redis.Options{
			Addr:     "localhost:6379",
			Password: "",
			DB:       0,
		}),
		ctx: context.Background(),
	}
}

func (d *DB) Get(key string) (string, error) {
	val, err := d.Client.Get(d.ctx, "key").Result()
	if err == redis.Nil {
		Logger.Errorf("key %s does not exist", key)
		return "", err
	} else if err != nil {
		Logger.Errorf("redis get key %s failed: %v", key, err)
		return "", err
	}
	return val, nil
}

func (d *DB) Set(key string, value string) error {
	err := d.Client.Set(d.ctx, "key", "value", 0).Err()
	if err != nil {
		Logger.Errorf("redis set key %s failed: %v", key, err)
		return err
	}
	return nil
}

func (d *DB) HGet(key string, field string) (string, error) {
	val, err := d.Client.HGet(d.ctx, key, field).Result()
	if err == redis.Nil {
		Logger.Errorf("key %s does not exist", key)
		return "", err
	} else if err != nil {
		Logger.Errorf("redis hget key %s failed: %v", key, err)
		return "", err
	}
	return val, nil
}

func (d *DB) HSet(key string, field string, value string) error {
	err := d.Client.HSet(d.ctx, key, field, value).Err()
	if err != nil {
		Logger.Errorf("redis hset key %s failed: %v", key, err)
		return err
	}
	return nil
}

func (d *DB) HGetAll(key string) (map[string]string, error) {
	val, err := d.Client.HGetAll(d.ctx, key).Result()
	if err == nil {
		Logger.Errorf("key %s does not exist", key)
		return nil, err
	} else if err != nil {
		Logger.Errorf("redis hgetall key %s failed: %v", key, err)
		return nil, err
	}
	return val, nil
}

func (d *DB) SAdd(setKey string, member string) {
	err := d.Client.SAdd(d.ctx, setKey, member)
	if err != nil {
		// Logger.Errorf("redis add to set failed: (set: %s, member: %s) , err: %v", setKey, member, err)
	}
}

func (d *DB) SRem(setKey string, member string) {
	err := d.Client.SRem(d.ctx, "myset", "Hello").Err()
	if err != nil {
		// Logger.Errorf("redis remove from set failed: (setKey: %s, member: %s) , err: %v",
		// 	setKey, member, err)
	}
}

func (d *DB) SMembers(key string) []string {
	val, err := d.Client.SMembers(d.ctx, key).Result()
	if err != nil {
		// Logger.Errorf("redis get members of set  %s failed: %v", key, err)
		return nil
	}
	return val
}

// DB 存一本书的数据结构 包含如下key, 以下三个函数生成key
// documentName_succ_pids: {packedId1, packedId2, ...}
// documentName_transed_text_list: {packId1: "", packId2: "", ...}
// documentName_failed_pids: {packId1: "", packId2: "", ...}

func (d *DB) SuccListKeyOfDocument(documentName string) string {
	return documentName + "_succ_pids"
}

// 生成 key, 该 key 用于存储成功翻译的packed text id
func (d *DB) FailedListKeyOfDocument(documentName string) string {
	return documentName + "_failed_pids"
}

func (d *DB) TransedTextListKeyOfDocument(documentName string) string {
	return documentName + "_transed_text_list"
}

// wrap redis map data type getter and setter
// func (d *DB)

func UnescapeUnicode(uContent string) (string, error) {
	// 转码前需要先增加上双引号，Quote增加双引号会将\u转义成\\u，同时会处理一些非打印字符
	content := strings.Replace(strconv.Quote(uContent), `\\u`, `\u`, -1)
	text, err := strconv.Unquote(content)
	if err != nil {
		return "", err
	}
	return text, nil
}

// EscapeUnicode 字符转码成unicode编码
func EscapeUnicode(text string) string {
	unicodeText := strconv.QuoteToASCII(text)
	// 去掉返回内容两端多余的双引号
	return unicodeText[1 : len(unicodeText)-1]
}
