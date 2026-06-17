#!/bin/bash
# RunPod 환경에서 Qwen3.5-4B 베이스 모델과 파인튜닝(QLoRA) 모델의 성능을 비교하는 평가 스크립트

echo "=========================================="
echo "1. LM-Eval 및 필수 패키지 설치"
echo "=========================================="
pip install lm-eval==0.4.2 typing_extensions --upgrade

echo "=========================================="
echo "2. 파인튜닝 전 (Base Model) 벤치마크 실행"
echo "=========================================="
# 한국어 언어 이해능력 평가(haerae)와 추론(arc_challenge) 포함
lm_eval --model hf \
    --model_args pretrained=Qwen/Qwen3.5-4B \
    --tasks arc_challenge,haerae \
    --device cuda:0 \
    --batch_size 4 \
    --output_path ./eval_results/base_v2

echo "=========================================="
echo "3. 파인튜닝 후 (QLoRA Model) 벤치마크 실행"
echo "=========================================="
lm_eval --model hf \
    --model_args pretrained=Qwen/Qwen3.5-4B,peft=./models/qwen3.5-4b-naming-qlora-v2 \
    --tasks arc_challenge,haerae \
    --device cuda:0 \
    --batch_size 4 \
    --output_path ./eval_results/finetuned_v2

echo "=========================================="
echo "평가가 완료되었습니다! ./eval_results/base_v2 와 ./eval_results/finetuned_v2 의 결과를 비교하세요."
echo "=========================================="
