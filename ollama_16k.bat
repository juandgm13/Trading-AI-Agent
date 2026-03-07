@echo off
REM Establecer contexto a 16k
set OLLAMA_CONTEXT_LENGTH=16384

REM Lanzar Ollama
ollama serve

pause