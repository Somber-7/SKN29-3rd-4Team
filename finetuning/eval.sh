#!/bin/bash
# RunPod 환경에서 파인튜닝 전/후 LM-Eval 벤치마크를 실행하는 스크립트입니다.

echo "=========================================="
echo "1. LM-Eval 패키지 설치"
echo "=========================================="
pip install lm-eval==0.4.2 typing_extensions --upgrade

echo "=========================================="
echo "2. 파인튜닝 전 (Base Model) 벤치마크 실행"
echo "=========================================="
lm_eval --model hf \
    --model_args pretrained=Qwen/Qwen3.5-4B \
    --tasks arc_easy,arc_challenge \
    --device cuda:0 \
    --batch_size 4 \
    --output_path ./eval_results/base

echo "=========================================="
echo "3. 파인튜닝 후 (QLoRA Model) 벤치마크 실행"
echo "=========================================="
# 모델이 저장된 경로(./models/qwen3.5-4b-naming-qlora)를 바라보도록 설정
lm_eval --model hf \
    --model_args pretrained=Qwen/Qwen3.5-4B,peft=./models/qwen3.5-4b-naming-qlora \
    --tasks arc_easy,arc_challenge \
    --device cuda:0 \
    --batch_size 4 \
    --output_path ./eval_results/finetuned

echo "=========================================="
echo "평가가 모두 완료되었습니다! ./eval_results 폴더의 결과를 확인하세요."
echo "=========================================="
