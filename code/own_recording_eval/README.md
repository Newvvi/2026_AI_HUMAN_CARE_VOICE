# code/own_recording_eval/ — 직접 녹음으로 평가하는 코랩 노트북 (11/07~11/08)

설명과 결과는 [../../docs/11_VOICE_ASR_TRACK.md](../../docs/11_VOICE_ASR_TRACK.md)를 보세요.
녹음 파일은 저장소에 없습니다 (개인 목소리, `.gitignore`). 구글 드라이브의 `6_own` 폴더(학습용, 시험용, `응급` 폴더, `장시간` 폴더) 구조를 가정합니다.
노트북은 가짜 데이터(가짜 YAMNet·Whisper·모델)로 흐름을 시험했고, 코랩에서 실제로 돌린 결과는 [11](../../docs/11_VOICE_ASR_TRACK.md)에 있습니다.

| 파일 | 런타임 | 하는 일 |
|---|---|---|
| `check_recordings.py` | PC | 녹음 파일의 음량, 찌그러짐, 잡음, 고음 잡음, 끊김, 좌우 위상을 점검 (`pip install numpy imageio-ffmpeg`) |
| `04_eval_own_recordings.ipynb` | CPU | 녹음을 16kHz 모노로 바꾸고 YAMNet 임베딩 → 현재 소리 모델의 일상 소리 오경보, 학습용 녹음을 더한 재학습의 전/후 |
| `05_retrain_all_own_and_final_test.ipynb` | CPU | 시험용까지 학습에 넣고 어려운 정상 소리에 가중치, `연속`/`3시간`/`장시간` 녹음은 최종 시험으로만 사용, `응급` 폴더는 학습에서 제외 |
| `06_eval_urgent_recording.ipynb` | CPU | `응급` 폴더의 "도와주세요" 녹음에서 모델별 감지한 발화 수 |
| `07_final_test_C_and_whisper.ipynb` | GPU 권장 | 연속 3시간(C)의 오경보 vs 재현율 표(소리 모델 3종), Whisper로 단어 인식 시험 |
| `08_whisper_sizes_and_sound_path.ipynb` | GPU 권장 | Whisper tiny/base/small 비교, 다른 녹음 오경보, 소리 모델의 종류별(신음·쿵 등) 재현율 |
| `09_asr_final_and_pi_bench.ipynb` | GPU 권장 | 고친 단어 규칙, 반복 규칙, 음량 게이트, 파이4 속도 시험용 구간 파일과 스크립트 만들기 |

- 노트북 04~09는 이전 노트북이 만든 캐시(`own_embeddings_v1.pkl`)를 이어서 씁니다. 순서대로 실행하세요.
- 코랩의 구글 드라이브에서는 한글 폴더·파일 이름이 자모가 풀어진 형태일 수 있어, 노트북은 이름을 정규화해서 비교합니다 ([09 #29](../../docs/09_CORRECTIONS_AND_LESSONS.md)).
