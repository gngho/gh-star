모델 비종속 CLI 에이전트 hermes-agent. 스킬·메모리를 파일로 남기는 학습 루프(agent/learning_graph.py)와 텔레그램·디스코드 게이트웨이, 7종 실행 백엔드(local·docker·ssh·singularity·modal·daytona·vercel)가 한 저장소에 들어 있습니다.

주목할 건 개발 속도입니다. v0.20.4→v0.20.5 한 구간에서만 약 746 커밋, 1,250여 개 파일, +111,500/−20,701 라인, 약 323개 PR이 병합됐습니다. 대신 회귀도 태그에 실립니다. 데스크톱 Bot Mode 무한 스피너(#92832), 한글에서 볼드 뒤 조사가 붙으면 `**`가 그대로 보이는 렌더링 문제(#92814) 등이 열려 있고, v0.21.0 전까지는 정리된 릴리스 노트도 없습니다. 도입 전에 이슈 목록부터 확인하세요.

https://github.com/NousResearch/hermes-agent
스타 234,515 · 라이선스 MIT · Python

#개발자 #오픈소스 #github #깃허브 #hermesagent #NousResearch #AI에이전트 #코딩에이전트 #python #파이썬 #CLI #MIT라이선스 #LLM #MCP #docker #devtools #개발도구 #자동화 #텔레그램봇 #디스코드봇
