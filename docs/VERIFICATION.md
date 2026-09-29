# 실제 검증 결과

검증일: 2026-09-28. 핵심 실행 환경: macOS arm64, Python 3.14.7, 프로젝트 전용 PostgreSQL(pgvector). Python 패키지 목록은 `reports/environment-packages.txt`입니다. 이는 측정 환경 기록이며 모든 플랫폼용 lock 파일은 아닙니다.

| 항목 | 확인한 결과 | 근거 |
|---|---|---|
| 전체 데모 | 생성·수집·검증·적재·재실행·보고서 명령 정상 종료 | demo_run.log, demo_evidence.json |
| 첫 적재 | 34개 파티션의 삽입·중복·격리 기록 보존 | initial_load_evidence.json |
| 금액·건수 | 6,000건, 결제 203,049,000원, 환불 7,937,000원, 순수납 195,112,000원; 정답·마트 일치 | final_verification.json |
| 자동 테스트 | 19개 통과, 최근 실행 13.19초 | tests.log |
| 실패 처리 | 트랜잭션 롤백, 해시 변조 차단, 이벤트 충돌, 과도 환불, 부모 도착 후 복구 | tests/test_pipeline.py |
| 집계 | 분할 결제 fan-out 방지, 세션 경계, 늦은 환불, 전체 재실행 불변 | tests/test_pipeline.py |
| Airflow | 4개 태스크 DAG test 성공, import error 없음 | airflow_test.log, airflow_imports.log |
| 성능 | 20만 행, 6개 엔진, 각 3회; 집계 결과 동일 | benchmark.json |
| SQL | 인덱스 전후 실행계획, JSONB 컬럼 승격 동등성, 분석 쿼리 실행 | sql.json |
| 프로파일 | 통계·문서·이미지·품질·결측 주입·엔티티 해상도 실험 | profile.json |
| 검색 | pgvector ANN과 exact 비교; MiniLM 80상품·4질의 실행 | search.json, semantic.json |
| Redis·Neo4j | 캐시 miss/hit, 만료, 무효화; 상품 80개와 판매자 2홉 탐색 | storage.json |
| 대시보드 | 앱 필터 1,987건, 역전 날짜 오류 안내, 초기화, 메뉴 이동, 브랜드 홈 이동, 콘솔 오류 없음 | 직접 브라우저 확인 |

근거 파일은 별도 표기가 없으면 `reports/` 아래에 있습니다. 대시보드는 1280px DOM 폭에서 가로 넘침이 없음을 확인했습니다. 모든 브라우저·모바일 기기를 테스트한 것은 아닙니다.

## 실험을 해석할 때

Pandas eager 집계 중앙값은 약 0.039초, Polars lazy는 0.007초, Parquet는 0.004초였습니다. 서로 다른 입력 포맷과 변환 비용 제외 조건을 고려해야 하며 이를 서비스 전체의 성능 향상률로 주장하지 않습니다. peak RSS는 별도 프로세스의 고수위 메모리이며 기본 라이브러리 비용을 포함합니다. 반복 실행은 OS 캐시 영향을 받습니다.

MiniLM 수작업 질의 4개 중 상위 1개 상품의 분류 일치는 3개였습니다. ‘따뜻한 커피를 담아 출근’ 질의가 헤드폰을 반환하는 실패 사례도 그대로 공개했습니다. 이는 개선할 사례이며 일반 검색 정확도 75%라는 주장을 뒷받침하는 평가셋이 아닙니다.

## 미검증·제한 사항

- MongoDB: 현재 Docker 커널 호환성 문제로 기동 실패. 구현 코드는 있지만 실제 조회 동등성 검증은 blocked입니다.
- Selenium: 동적 상품 페이지와 명시적 대기 수집 코드는 제공하나 실제 Selenium 브라우저 실행은 하지 않았습니다. API와 BeautifulSoup 경로는 실행했습니다.
- Airflow: `dags test` 성공을 확인했습니다. 장기간 스케줄러 정기 실행·장애 복구 운영은 검증하지 않았습니다.
- GitHub Actions: 워크플로 파일을 제공하지만 원격 저장소에 게시하거나 CI를 실행하지 않았습니다.
- DB 백업·복구: 안내 명령만 제공하고 복원 실험은 하지 않았습니다.
- 실제 외부 고객 데이터, 외부 원장 대조, 분산 부하, 다중 서버 장애, 운영 보안·접근 제어는 범위 밖입니다.
