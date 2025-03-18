package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"slices"
	"strconv"
	"strings"

	"github.com/caarlos0/env/v11"
	"github.com/sashabaranov/go-openai"
	"github.com/yomorun/yomo/serverless"
)

type Config struct {
	Port                 int    `env:"PORT" envDefault:"13000"`
	LlmBaseUrl           string `env:"LLM_BASE_URL" envDefault:"http://127.0.0.1:8080/v1"`
	LlmToken             string `env:"LLM_TOKEN" envDefault:""`
	EmbBaseUrl           string `env:"EMB_BASE_URL" envDefault:"http://127.0.0.1:8080/v1"`
	EmbToken             string `env:"EMB_TOKEN" envDefault:""`
	ModelWithoutThinking string `env:"MODEL_WITHOUT_THINKING" envDefault:"Qwen/Qwen2.5-7B-Instruct"`
	ModelEmb             string `env:"MODEL_EMB" envDefault:"BAAI/bge-m3"`
	ModelRerank          string `env:"MODEL_RERANK" envDefault:"BAAI/bge-reranker-v2-m3"`
	TopEmb               int    `env:"TOP_EMB" envDefault:"25"`
	TopRerank            int    `env:"TOP_RERANK" envDefault:"5"`
	SummaryFile          string `env:"SUMMARY_FILE" envDefault:"./summary.txt"`
	MarkdownDir          string `env:"MARKDOWN_DIR" envDefault:"./markdown"`
	Topic                string `env:"TOPIC" envDefault:"所有"`
}

type Document struct {
	DocId   int
	Title   string
	Content string
	Summary string
}

var (
	cfg           *Config
	allDocIds     map[int]int
	allDocuments  []*Document
	allEmbeddings []openai.Embedding
)

type Parameter struct {
	Question string `json:"question" jsonschema:"description=用户提出的原始问题。如果是多轮回话，请分析上下文后给出最终的完整问题。"`
}

func Description() string {
	return fmt.Sprintf("当用户查询%s问题时调用此函数", cfg.Topic)
}

func InputSchema() any {
	return &Parameter{}
}

func init() {
	c, err := env.ParseAs[Config]()
	if err != nil {
		log.Fatalln(err)
	}
	cfg = &c
	fmt.Println("config:", cfg)
}

func Init() error {
	titles := make(map[int]string)
	files, err := os.ReadFile(fmt.Sprintf("%s/files.txt", cfg.MarkdownDir))
	if err == nil {
		lines := strings.Split(string(files), "\n")
		for _, line := range lines {
			strs := strings.SplitN(line, ":", 2)
			if len(strs) != 2 {
				continue
			}
			v, err := strconv.Atoi(strs[0])
			if err == nil {
				title := strs[1]
				for _, suffix := range []string{
					".pdf",
					".doc",
					".docx",
					".xls",
					".xlsx",
					".ppt",
					".pptx",
				} {
					title = strings.TrimSuffix(title, suffix)
				}
				titles[v] = title
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}

	file, err := os.Open(cfg.SummaryFile)
	if err != nil {
		return err
	}
	defer file.Close()

	idx := 0
	allDocIds = make(map[int]int)
	summaries := []string{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		strs := strings.SplitN(scanner.Text(), ":", 2)
		if len(strs) != 2 {
			continue
		}

		docId, err := strconv.Atoi(strs[0])
		if err != nil {
			return err
		}
		summary := strs[1]

		content, err := os.ReadFile(fmt.Sprintf("%s/%d.md", cfg.MarkdownDir, docId))
		if err != nil {
			return err
		}

		allDocIds[docId] = idx
		doc := &Document{
			DocId:   docId,
			Content: string(content),
			Summary: summary,
		}
		if title, ok := titles[docId]; ok {
			doc.Title = title
		}
		allDocuments = append(allDocuments, doc)
		summaries = append(summaries, summary)

		idx += 1
		fmt.Printf("doc %d: %s\n", doc.DocId, doc.Title)
	}

	embs, err := calcEmbeddings(summaries)
	if err != nil {
		return err
	}
	allEmbeddings = embs

	fmt.Printf("total %d documents\n", len(summaries))

	return nil
}

func Handler(ctx serverless.Context) {
	var msg Parameter
	err := ctx.ReadLLMArguments(&msg)
	if err != nil {
		fmt.Println("ReadLLMArguments error:", err)
		return
	}

	result, err := RunRAG(msg.Question)
	if err != nil {
		fmt.Println("error:", err)
		return
	}

	ctx.WriteLLMResult(result)
}

func RunRAG(question string) (string, error) {
	fmt.Printf("question: %s\n", question)

	resEmb, err := findSimilar(question, allEmbeddings, cfg.TopEmb)
	if err != nil {
		return "", err
	}

	docIds := []int{}
	summaries := []string{}
	for _, idx := range resEmb {
		doc := allDocuments[idx]
		docIds = append(docIds, doc.DocId)
		summaries = append(summaries, doc.Summary)
	}
	fmt.Printf("similar docs (embedding): %v\n", docIds)

	resRerank, err := rerank(question, summaries, cfg.TopRerank)
	if err != nil {
		return "", err
	}

	docIdsRerank := []int{}
	for _, v := range resRerank.Results {
		docIdsRerank = append(docIdsRerank, docIds[v.Index])
	}
	fmt.Printf("similar docs (rerank): %v\n", docIdsRerank)

	result := fmt.Sprintf("检索到以下%d篇文档：\n\n", len(docIdsRerank))
	for i, docId := range docIdsRerank {
		idx := allDocIds[docId]
		doc := allDocuments[idx]
		fmt.Printf("doc %d|%s:\n%s\n", docId, doc.Title, doc.Summary)
		result += fmt.Sprintf("第%d篇文档", i+1)
		if len(doc.Title) > 0 {
			result += fmt.Sprintf("，标题为「%s」", doc.Title)
		}
		result += fmt.Sprintf("：\n\n%s\n\n", doc.Content)
	}

	return result, nil
}

type Score struct {
	Index int
	Value float32
}

// 通过余弦相似度查询相似语料
func findSimilar(query string, embeddings []openai.Embedding, topN int) ([]int, error) {
	if topN > len(embeddings) {
		topN = len(embeddings)
	}

	embs, err := calcEmbeddings([]string{query})
	if err != nil {
		return nil, err
	}
	emb := embs[0]

	dotA, err := emb.DotProduct(&emb)
	if err != nil {
		return nil, err
	}
	if dotA <= 0 {
		return nil, errors.New("embedding is zero")
	}
	normA := float32(math.Sqrt(float64(dotA)))

	scores := make([]Score, len(embeddings))
	for i, v := range embeddings {
		dotB, err := v.DotProduct(&v)
		if err != nil {
			return nil, err
		}
		if dotB <= 0 {
			return nil, fmt.Errorf("metric embedding %d is zero", i)
		}
		normB := float32(math.Sqrt(float64(dotB)))

		dot, err := emb.DotProduct(&v)
		if err != nil {
			return nil, err
		}

		scores[i] = Score{
			Index: v.Index,
			Value: dot / normA / normB,
		}
	}

	slices.SortFunc(scores, func(a Score, b Score) int {
		if a.Value > b.Value {
			return -1
		} else if a.Value < b.Value {
			return 1
		}
		return 0
	})

	res := make([]int, topN)
	for i := 0; i < topN; i++ {
		res[i] = scores[i].Index
	}

	return res, nil
}

// 计算输入语料的embedding值
func calcEmbeddings(input []string) ([]openai.Embedding, error) {
	if len(input) == 0 {
		return nil, errors.New("input is empty")
	}

	config := openai.DefaultConfig(cfg.EmbToken)
	config.BaseURL = cfg.EmbBaseUrl
	response, err := openai.NewClientWithConfig(config).CreateEmbeddings(
		context.Background(),
		openai.EmbeddingRequestStrings{
			Input: input,
			Model: openai.EmbeddingModel(cfg.ModelEmb),
		},
	)
	if err != nil {
		return nil, err
	}
	if len(response.Data) != len(input) {
		return nil, errors.New("embedding length mismatch")
	}

	return response.Data, nil
}

type RerankRequest struct {
	Model     string   `json:"model"`
	Query     string   `json:"query"`
	Documents []string `json:"documents"`
	TopN      int      `json:"top_n"`
}

type RerankResponse struct {
	Results []struct {
		Index          int     `json:"index"`
		RelevanceScore float32 `json:"relevance_score"`
	} `json:"results"`
}

// 调用重排序模型
func rerank(query string, documents []string, topN int) (*RerankResponse, error) {
	buf, err := json.Marshal(&RerankRequest{
		Model:     cfg.ModelRerank,
		Query:     query,
		Documents: documents,
		TopN:      topN,
	})
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequest(http.MethodPost, cfg.EmbBaseUrl+"/rerank", bytes.NewReader(buf))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cfg.EmbToken)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, errors.New(resp.Status)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var msg RerankResponse
	err = json.Unmarshal(body, &msg)
	if err != nil {
		return nil, err
	}

	return &msg, nil
}
