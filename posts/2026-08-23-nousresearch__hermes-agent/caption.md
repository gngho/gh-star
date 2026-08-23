Nous Research의 hermes-agent. README가 말하는 '내장 학습 루프'가 문구가 아니라 실제 모듈로 존재한다 — curator.py 87KB, learning_graph, learning_mutations, memory_manager, skill_utils 48KB, 세션 검색용 hermes_state_search까지 각각 따로 있다. 다만 파일의 존재와 규모만 확인했고 동작 품질까지는 확인하지 못했다.

두 번째 축은 '랩톱에 묶이지 않는다'는 것. 메신저 게이트웨이가 별도 프로세스(hermes gateway)로 있고 matrix·dingtalk·feishu·teams·sms가 extra로, modal·daytona·vercel 샌드박스도 각각 extra로 붙는다. 코어 의존성은 openai SDK 하나뿐이라 쓰는 백엔드만 지연 설치된다.

공급망 정책도 눈에 띈다. pyproject.toml은 직접 의존성을 전부 ==X.Y.Z로 고정하고, 그 이유(PyPI mistralai 2.4.6을 노린 Mini Shai-Hulud 웜 사건)를 주석으로 적어뒀다. uv exclude-newer = "14 days"로 갓 올라온 릴리스는 기본 차단한다.

속도는 장점이자 리스크다. v2026.8.19 릴리스 노트 기준 v0.20.4 이후 하루 남짓한 창에서 약 746 커밋, 약 323 PR. 반대로 오픈 이슈 상당수가 업데이트 직후 깨진 사례고(데스크톱 IPC 브리지, .desktop 파일의 venv 우회, cron list ValueError), v0.20.x 정식 큐레이션 노트는 v0.21.0으로 미뤄져 있다. 가벼운 CLI 도구로 오해하면 안 된다 — 데스크톱 앱, Electron/React 워크스페이스, cron, MCP 서버, 플러그인까지 한 저장소에 들어 있는 대형 플랫폼에 가깝다.

https://github.com/NousResearch/hermes-agent
스타 234,523 (오늘 +443) · 라이선스 MIT · Python

#hermesagent #NousResearch #AI에이전트 #코딩에이전트 #오픈소스 #깃허브 #python #파이썬 #개발자 #개발도구 #CLI #터미널 #LLM #MCP #공급망보안 #의존성관리 #devtools #opensource #github #aiagent #자동화 #사이드프로젝트 #개발자일상 #프로그래밍
