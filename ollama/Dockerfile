FROM ollama/ollama:latest

# Accept space-separated list of Ollama models to pre-pull at build time
ARG OLLAMA_MODELS="mimo-v2.6:9b qwen3:1.7b"
ENV OLLAMA_MODELS=${OLLAMA_MODELS}

RUN ollama serve & SERVER_PID=$! && \
    sleep 5 && \
    for model in ${OLLAMA_MODELS}; do \
        echo "Pulling Ollama model: ${model}" && \
        ollama pull "${model}"; \
    done && \
    kill $SERVER_PID

CMD ["serve"]
