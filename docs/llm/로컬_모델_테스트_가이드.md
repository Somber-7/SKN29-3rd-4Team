# 🚀 파인튜닝 sLLM(Qwen) API 연동 가이드 (팀원 공유용)

안녕하세요 팀원 여러분! 🎉
현재 저희 팀이 직접 파인튜닝한 **"작명 특화 Qwen3.5-4B (LoRA) 모델"**이 제 RunPod 환경에 클라우드 API 서버 형태로 가동되고 있습니다.

각자 작업하고 계신 로컬 컴퓨터의 **랭그래프(LangGraph) 파이프라인 코드**에 저희 모델을 연동하여 테스트해 보실 수 있도록 접속 주소와 코드를 공유해 드립니다!

---

## 🔗 접속 정보 (API Endpoint)
현재 모델 서버는 Cloudflare 터널링을 통해 외부로 개방되어 있습니다.
* **API Base URL**: `https://minutes-interest-want-agent.trycloudflare.com/v1`
* **지원 포맷**: 100% OpenAI API 호환 (LangChain의 `ChatOpenAI` 모듈을 그대로 쓸 수 있습니다.)

> **[주의사항]**
> 위 주소는 제 RunPod 서버가 켜져 있는 동안만 유지되는 임시 터널링 주소입니다! 
> 만약 코드를 돌렸는데 `Connection Error`가 발생한다면 서버가 꺼진 것이니 저에게 다시 켜달라고 요청해 주세요! 🙋‍♂️

---

## 💻 랭그래프(LangChain) 파이프라인 수정 방법

팀원분들의 로컬 파이프라인 코드 중에서, LLM 모델을 정의하는 `ChatOpenAI` 부분을 찾아서 아래 코드로 싹 덮어쓰기 해주시면 됩니다. (환경 변수 등 다른 코드는 안 건드려도 됩니다!)

```python
from langchain_openai import ChatOpenAI

# ----------------------------------------------------
# [기존 코드] gpt-4o-mini 등을 호출하던 부분을 주석 처리하고
# [신규 코드] 아래 코드로 교체해 주세요!
# ----------------------------------------------------
sllm_model = ChatOpenAI(
    model="qwen-lora",  # 파인튜닝 모델 식별자 (서버에 지정된 이름)
    openai_api_key="sk-dummy-key",  # 아무 문자열이나 넣어도 패스됩니다.
    openai_api_base="https://minutes-interest-want-agent.trycloudflare.com/v1",  # RunPod 터널링 주소
    temperature=0.7,     # 창의성 조절 (추천값: 0.7)
    max_tokens=250,      # 답변 길이 (너무 길면 끊길 수 있으니 250~300 추천)
)

# 잘 작동하는지 간단한 테스트 코드
if __name__ == "__main__":
    print("🚀 클라우드 파인튜닝 모델로 질문 전송 중...")
    response = sllm_model.invoke("김씨 성을 가진 남자아이 이름 1개 지어줘")
    print("\n✅ 모델 응답 결과:")
    print(response.content)
```

---

## 💡 기대 효과 및 테스트 포인트
이 모델은 기존에 프롬프트 엔지니어링으로 억지로 포맷을 맞추던 GPT와 달리, **"이름 작명 마크다운 포맷"을 네이티브로 완전히 학습(정답률 85% 이상)**한 녀석입니다.

* RAG 파이프라인을 거쳐서 검색된 `[참고 정보]` 문맥(Context)을 이 모델에 프롬프트로 쏴주면, 모델이 해당 정보를 찰떡같이 참고해서 예쁜 포맷으로 답변을 뱉어내는지 확인해 보세요!
* 벤치마크 테스트(ARC) 결과 **기존 상식과 추론 능력(Reasoning)도 전혀 망가지지 않았음**이 증명되었으니, 안심하고 복잡한 프롬프트를 던지셔도 됩니다!
