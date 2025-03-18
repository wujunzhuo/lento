package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/sashabaranov/go-openai"
)

var (
	openaiClient *openai.Client
)

func chatApiHandler(c *gin.Context) {
	var request openai.ChatCompletionRequest
	err := c.ShouldBindJSON(&request)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// 缓存用户原始的模型和系统提示
	systemPrompt := ""
	if request.Messages[0].Role == openai.ChatMessageRoleSystem {
		systemPrompt = request.Messages[0].Content
	}
	model := request.Model

	// 调用非推理模型，从聊天历史中提取用户原始问题
	request.Model = cfg.ModelWithoutThinking
	request.Stream = false
	chatHistory := ""
	for i, msg := range request.Messages {
		if msg.Role == openai.ChatMessageRoleSystem {
			continue
		}
		chatHistory += fmt.Sprintf("%d. [role=%s] %s\n\n", i, msg.Role, msg.Content)
	}
	request.Messages = []openai.ChatCompletionMessage{
		{
			Role:    openai.ChatMessageRoleSystem,
			Content: "请根据以下提供的聊天记录历史，总结出一条用户的原始问题。",
		},
		{
			Role:    openai.ChatMessageRoleUser,
			Content: chatHistory,
		},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	response, err := openaiClient.CreateChatCompletion(ctx, request)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	question := response.Choices[0].Message.Content

	// 调用RAG模型，获取检索结果
	result, err := RunRAG(question)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// 结合用户问题和检索结果，调用大模型，获取最终的输出结果
	request.Model = model
	request.Stream = true // 仅支持流式响应
	request.Messages = []openai.ChatCompletionMessage{
		{
			Role:    openai.ChatMessageRoleSystem,
			Content: systemPrompt,
		},
		{
			Role:    openai.ChatMessageRoleUser,
			Content: fmt.Sprintf("请根据以下检索到的信息，回答用户的原始问题：%s\n\n%s", question, result),
		},
	}
	ctx1, cancel1 := context.WithTimeout(context.Background(), 300*time.Second)
	defer cancel1()
	streamResponse, err := openaiClient.CreateChatCompletionStream(ctx1, request)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// SSE 流式返回
	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Stream(
		func(w io.Writer) bool {
			buf, err := streamResponse.RecvRaw()
			if err != nil {
				if err != io.EOF {
					c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
				}
				return false
			}
			w.Write([]byte("data: "))
			w.Write(buf)
			w.Write([]byte("\n\n"))
			return true
		},
	)
	c.Writer.Write([]byte("data: [DONE]\n\n"))
}

func main() {
	err := Init()
	if err != nil {
		log.Fatalln(err)
	}

	config := openai.DefaultConfig(cfg.LlmToken)
	config.BaseURL = cfg.LlmBaseUrl
	openaiClient = openai.NewClientWithConfig(config)

	router := gin.Default()
	router.POST("/v1/chat/completions", chatApiHandler)

	router.Run(fmt.Sprintf(":%d", cfg.Port))
}
