
// ---------------------------------------------------------
// 1. 데이터와 공통 표시 함수
// 이 파일은 index.html의 마지막에서 data.js 다음에 실행됩니다.
// 따라서 DOM 요소들과 window.SHOPSCOPE_DATA가 준비되어 있다는 전제로 시작합니다.
// strict 모드는 선언하지 않은 변수 사용 같은 실수를 오류로 드러냅니다.
// ---------------------------------------------------------
'use strict';
// report.build_report가 만든 data.js를 읽습니다. DB를 실시간 조회하는 화면이 아니라 저장된 결과를 탐색합니다.

// D는 전체 스냅샷의 짧은 이름입니다.
// D.daily=날짜/채널별 집계, D.products=상위 상품, D.quality=격리 요약, D.labs=실험 결과입니다.
const D = window.SHOPSCOPE_DATA;
// querySelector의 축약 함수입니다. index.html의 id/class가 이 선택자와 연결됩니다.

// 화살표 함수 s => ... 는 선택자 문자열을 받아 DOM 요소 하나를 반환합니다.
// 예: $("#net")은 id="net"인 요소입니다. 없으면 null이므로 HTML의 ID와 맞아야 합니다.
const $ = s => document.querySelector(s);
// HTML 문자열에 들어갈 데이터의 특수문자를 치환합니다. textContent는 브라우저가 텍스트로 다루지만 innerHTML은 마크업으로 해석합니다.

// v ?? ""는 null/undefined일 때만 빈 문자열로 바꿉니다.
// & < > " '를 HTML 엔티티로 치환하여 데이터가 태그로 해석되는 것을 줄입니다.
// 문자열을 innerHTML에 넣는 위치에서 호출하며, 숫자/고정 문자열과는 구분합니다.
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// 숫자 변환·천 단위 표시와 한국어 원화 단위 표시를 분리하여 여러 카드와 표에서 재사용합니다.

// n은 값이 비었을 때 0으로 바꾼 뒤 Number로 변환합니다.
// fmt는 한국어 로케일로 천 단위 구분을 넣습니다. 예: 12345 → "12,345".
// 임의의 잘못된 문자열까지 정상 숫자로 고치는 검증 함수는 아닙니다.
const n = v => Number(v || 0), fmt = v => n(v).toLocaleString('ko-KR');

// 삼항 연산자를 순서대로 써 1억 이상은 억 원, 1만 이상은 만 원, 나머지는 원으로 표시합니다.
// 보기 좋은 축약 표시이며 정확한 원 단위 값은 fmt로 따로 보여 줄 수 있습니다.
const money = v => n(v) >= 1e8 ? (v / 1e8).toFixed(2) + '억 원' : n(v) >= 1e4 ? (v / 1e4).toLocaleString('ko-KR', { maximumFractionDigits: 1 }) + '만 원' : fmt(v) + '원';

// 사용자가 선택한 채널 상태입니다. all/app/web/market 중 버튼의 data-channel 값이 들어갑니다.
// 상태가 바뀌면 render를 다시 호출하여 화면을 현재 선택에 맞춥니다.
let channel = 'all';

// 데이터 파일을 읽지 못했으면 사용 안내를 표시하고 실행을 중단합니다.
// 스냅샷 없이 아래 D.daily 등에 접근하면 더 불명확한 오류가 나므로 처음에 검사합니다.
if (!D) { document.querySelector('main').textContent = '데이터가 없습니다. python -m shopscope demo 실행 후 다시 열어주세요.'; throw new Error('Missing snapshot') }
// YYYY-MM-DD 형식이므로 문자열 정렬이 날짜 정렬과 같습니다. Set으로 같은 날짜를 한 번만 남깁니다.

// map으로 날짜만 꺼내고 Set으로 중복을 없앤 뒤 ...로 다시 배열에 펼칩니다.
// YYYY-MM-DD 형식은 문자열 정렬이 날짜순과 같아 sort()를 그대로 사용할 수 있습니다.
const dates = [...new Set(D.daily.map(r => r.day))].sort();

// 가장 이른 날짜/가장 늦은 날짜를 필터 기본값으로 씁니다.
// at(-1)은 배열 마지막 원소이고 배열이 비면 빈 문자열을 넣습니다.
$('#from').value = dates[0] || ''; $('#to').value = dates.at(-1) || '';
const colors = { app: '#237f70', web: '#93b8a1', market: '#d9e7bf' }, names = { app: '앱', web: '웹', market: '마켓' };
// 선택 기간과 채널을 적용합니다. all=true는 기간은 유지하면서 채널 조건만 해제해 전체 채널 도넛을 만듭니다.

// ---------------------------------------------------------
// 2. 선택 조건으로 데이터 고르기
// Array.filter는 조건이 true인 행만 새 배열에 담습니다.
// all=true이면 채널 조건만 무시하고 날짜 범위는 그대로 적용합니다.
// 원본 D.daily를 수정하지 않으므로 초기화할 때 다시 원본을 사용할 수 있습니다.
// ---------------------------------------------------------
function filtered(all = false) { return D.daily.filter(r => (all || channel === 'all' || r.channel === channel) && r.day >= $('#from').value && r.day <= $('#to').value) }
// 사용자가 조건을 바꿀 때마다 집계→카드→차트→도넛 순서로 다시 그립니다. 날짜 역전이면 빈 결과로 처리합니다.

// render는 여러 UI 요소를 한 번에 현재 상태와 동기화합니다.
// 잘못된 날짜 범위에서는 rows를 비워 집계 값/그래프가 이전 조건의 값을 남기지 않게 합니다.
function render() {
    const invalid = $('#from').value > $('#to').value;
    $('#filter-error').textContent = invalid ? '시작 날짜는 종료 날짜보다 늦을 수 없습니다.' : '';

    // reduce는 행들을 순회하면서 누적 객체 a를 갱신합니다.
    // a[k]가 처음에는 없으므로 0으로 시작하고 orders/net/refunded 등의 열을 합칩니다.
    // 결과 totals는 현재 필터에 해당하는 합계입니다.
    const rows = invalid ? [] : filtered(); const totals = rows.reduce((a, r) => { for (const k of ['orders', 'net', 'refunded', 'captured', 'delivered', 'late']) a[k] = (a[k] || 0) + n(r[k]); return a }, {});

    // textContent는 값을 HTML이 아닌 텍스트로 넣습니다.
    // 큰 금액은 화면에서 축약하되 title 속성에는 정확한 원 단위 값을 넣어 마우스 오버로 확인할 수 있게 합니다.
    $('#net').textContent = money(totals.net); $('#net').title = fmt(totals.net) + '원'; $('#orders').textContent = fmt(totals.orders) + '건'; $('#refund').textContent = money(totals.refunded);

    // 환불 비중 = 환불액/결제액 × 100입니다.
    // 결제액이 0이면 나눗셈 대신 "결제액 없음"을 표시합니다.
    $('#refund-share').textContent = totals.captured ? '결제액 대비 ' + (100 * totals.refunded / totals.captured).toFixed(1) + '%' : '결제액 없음';

    // 정시 배송률 = (1 - 지연 완료 주문/배송 완료 주문) × 100입니다.
    // 분모가 전체 주문 수가 아니라 배송 완료 주문 수라는 점을 확인하세요. 미완료 주문은 여기서 지연으로 단정하지 않습니다.
    $('#on-time').textContent = totals.delivered ? ((1 - totals.late / totals.delivered) * 100).toFixed(1) + '%' : '—';
// 같은 날짜의 여러 채널 행을 한 날짜로 합칩니다. 현재 rows에 포함된 날짜만 표시하며 빈 날짜 보간은 하지 않습니다.

    // ??=는 해당 날짜의 누적 객체가 없을 때만 초기값을 넣습니다.
    // 서로 다른 채널의 같은 날짜 행을 합쳐 [날짜, {net, orders}] 형태의 그래프 입력으로 바꿉니다.
    const daily = {}; for (const r of rows) { daily[r.day] ??= { net: 0, orders: 0 }; daily[r.day].net += n(r.net); daily[r.day].orders += n(r.orders) }

    // Object.entries는 날짜 키와 합계 객체를 한 쌍으로 만든 배열입니다.
    // localeCompare로 날짜순 정렬한 후 chart에 넘깁니다.
    chart(Object.entries(daily).sort((a, b) => a[0].localeCompare(b[0])));
    $('#chart-period').textContent = $('#from').value + ' — ' + $('#to').value;
// 채널 비율을 누적 백분율 구간으로 바꾸고 conic-gradient로 도넛을 그립니다. 주문이 0이면 나눗셈을 피합니다.
    const counts = { app: 0, web: 0, market: 0 }; for (const r of invalid ? [] : filtered(true)) counts[r.channel] += n(r.orders);

    // 채널 주문 수 합계를 구한 뒤 채널마다 시작%와 끝%를 누적합니다.
    // 예: app 50%, web 30%, market 20%이면 구간은 0~50, 50~80, 80~100입니다.
    const sum = Object.values(counts).reduce((a, b) => a + b, 0); let pos = 0; const stops = Object.keys(counts).map(k => { const start = pos; pos += sum ? counts[k] / sum * 100 : 0; return `${colors[k]} ${start}% ${pos}%` });

    // conic-gradient에 위 백분율 구간을 넣으면 원을 채널 비율대로 나눌 수 있습니다.
    // 안쪽 흰 div가 가운데를 가려 도넛처럼 보입니다. 주문 0건이면 단색으로 표시합니다.
    $('#donut').innerHTML = `<div class="donut-ring" style="background:${sum ? 'conic-gradient(' + stops.join(',') + ')' : '#eef2eb'}"><div class="donut-center">${fmt(sum)}<small>전체 채널 주문</small></div></div>`;
    $('#channel-list').innerHTML = Object.keys(counts).map(k => `<div class="channel-row"><span><i class="dot" style="background:${colors[k]}"></i>${names[k]}</span><span>${fmt(counts[k])}건<em>${sum ? (100 * counts[k] / sum).toFixed(1) : 0}%</em></span></div>`).join('');
}
// 라이브러리 없이 SVG 문자열을 만듭니다. 순수납액 선과 주문 건수 막대는 서로 다른 스케일을 사용합니다.

// ---------------------------------------------------------
// 3. SVG 그래프 만들기
// SVG는 HTML 안에 좌표 기반 도형을 그리는 방식입니다.
// rows는 날짜순 [날짜, 합계 객체] 배열이며 막대는 주문 수, 선은 순수납액입니다.
// 두 지표의 스케일이 달라 도형 높이를 직접 같은 단위로 비교하면 안 됩니다.
// ---------------------------------------------------------
function chart(rows) {

    // 선택 결과가 없으면 빈 상태 안내를 넣고 return하여 아래 좌표 계산을 건너뜁니다.
    // 빈 배열의 최대값 계산 같은 불필요한 처리를 피하는 조기 반환입니다.
    if (!rows.length) { $('#chart').innerHTML = '<div class="empty">선택한 조건에 해당하는 주문이 없습니다.</div>'; return }

    // W/H는 SVG 기준 폭/높이, L/R/T/B는 여백, w/h는 실제 그릴 영역입니다.
    // max/maxO는 금액/주문수 각각의 축 상한입니다. 1.18/1.45를 곱해 위쪽 여유를 줍니다.
    const W = 640, H = 225, L = 49, R = 15, T = 15, B = 32, w = W - L - R, h = H - T - B, max = Math.max(...rows.map(r => r[1].net), 1) * 1.18, maxO = Math.max(...rows.map(r => r[1].orders), 1) * 1.45;
// 데이터 인덱스/금액을 화면 좌표로 바꿉니다. SVG는 아래로 갈수록 y가 커지므로 금액 높이를 빼서 위로 올립니다.

    // 한 날짜뿐이면 가운데에 놓고 여러 날짜면 인덱스를 같은 간격으로 나눕니다.
    // 날짜가 빠져 있어도 인접 행의 간격은 같으므로 실제 날짜 간격을 비례 표현한 축은 아닙니다.
    const x = i => L + (rows.length === 1 ? w / 2 : i * w / (rows.length - 1)), y = v => T + h - v / max * h;

    // viewBox는 실제 화면 크기와 무관한 내부 좌표계입니다. CSS width:100%로 크기를 바꿔도 비율이 유지됩니다.
    // defs의 linearGradient는 아래 polygon이 참조할 영역 색상 정의입니다.
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="일별 순수납액, 선 그래프 및 주문 건수 막대 그래프"><defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#83b6a3" stop-opacity=".23"/><stop offset="1" stop-color="#83b6a3" stop-opacity="0"/></linearGradient></defs>`;

    // 0부터 상한까지 네 수평 눈금과 금액 라벨을 그립니다.
    // SVG y축은 아래 방향이 양수이므로 높은 값일수록 T 쪽에 가까운 좌표를 사용합니다.
    for (let i = 0; i <= 3; i++) { let yy = T + h - i * h / 3; svg += `<line x1="${L}" y1="${yy}" x2="${W - R}" y2="${yy}" stroke="#edf1ed" stroke-dasharray="3 5"/><text x="0" y="${yy + 3}" font-size="9" fill="#9ca99f">${Math.round(max * i / 3 / 10000)}만</text>` }

    // 각 날짜에 주문 막대를 하나 그리고 title로 상세 수치를 넣습니다.
    // 날짜 라벨은 모두 그리면 겹치므로 대략 일곱 구간마다, 그리고 마지막 날짜에 표시합니다.
    for (let i = 0; i < rows.length; i++) { const [day, r] = rows[i]; const bw = Math.min(9, w / rows.length / 2), bh = r.orders / maxO * h; svg += `<rect x="${x(i) - bw / 2}" y="${T + h - bh}" width="${bw}" height="${bh}" rx="2" fill="#e0eae1"><title>${esc(day)} · 주문 ${r.orders}건 · 순수납액 ${fmt(r.net)}원</title></rect>`; if (i % Math.max(1, Math.ceil(rows.length / 7)) === 0 || i === rows.length - 1) svg += `<text x="${x(i)}" y="${H - 7}" text-anchor="middle" font-size="9" fill="#98a49b">${day.slice(5).replace('-', '.')}</text>` }

    // 모든 금액 점을 "x,y x,y ..." 문자열로 연결합니다.
    // polygon은 바닥까지 닫힌 면적, polyline은 점들을 잇는 선으로 같은 점 목록을 재사용합니다.
    const pts = rows.map((r, i) => `${x(i)},${y(r[1].net)}`).join(' '); svg += `<polygon points="${L},${T + h} ${pts} ${x(rows.length - 1)},${T + h}" fill="url(#area)"/><polyline points="${pts}" fill="none" stroke="#388f7b" stroke-width="2.3" stroke-linejoin="round"/>`;

    // 금액 선 위에 작은 원과 툴팁을 추가하고 최종 SVG를 DOM에 삽입합니다.
    // 이 구현은 저장된 합성 스냅샷의 날짜/숫자 형식을 전제로 합니다.
    rows.forEach((r, i) => { svg += `<circle cx="${x(i)}" cy="${y(r[1].net)}" r="3" fill="#388f7b"><title>${r[0]} · ${fmt(r[1].net)}원 · ${r[1].orders}건</title></circle>` }); $('#chart').innerHTML = svg + '</svg>'
}
// 메뉴별 문구를 한곳에 둡니다. 화면 전환은 새 페이지 요청 대신 .active 클래스와 URL 해시를 바꿉니다.

// ---------------------------------------------------------
// 4. 메뉴, 입력, 내려받기 이벤트
// 페이지별 제목/설명을 객체에 모아 화면 전환 때 같은 위치를 갱신합니다.
// 페이지 본문은 이미 HTML에 있으며 active 클래스만 바꿉니다.
// ---------------------------------------------------------
const titles = { overview: ['운영 개요', '숫자 뒤의 흐름까지, 한눈에.', '주문부터 환불, 배송까지. 검증된 데이터로 상점의 흐름을 살펴보세요.'], quality: ['데이터 품질', '좋은 분석은, 믿을 수 있는 데이터에서.', '무엇을 받아들였고 무엇을 보류했는지, 판단의 근거를 확인합니다.'], pipeline: ['파이프라인', '한 번의 성공보다, 반복 가능한 실행.', '수집 날짜와 실행 시각을 구분하고, 재시도와 늦은 도착에 대비합니다.'], research: ['성능 · 검색 실험', '선택의 이유를, 측정으로 설명합니다.', '빠른 도구를 고르기 전에 결과가 같은지부터 확인합니다.'], study: ['프로젝트 가이드', '완성된 코드가, 나의 설명이 되도록.', '한 주문의 여정을 따라가며 데이터 엔지니어링의 기본을 익힙니다.'] };

// classList.toggle(클래스, 조건)은 조건에 따라 클래스를 추가/제거합니다.
// history.replaceState로 #페이지를 기록하되 방문 기록을 계속 쌓지는 않습니다.
// 알 수 없는 페이지 이름이면 아무것도 바꾸지 않고 반환합니다.
function navigate(page) { if (!titles[page]) return; document.querySelectorAll('.page').forEach(e => e.classList.toggle('active', e.id === page)); document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.page === page)); $('#page-label').textContent = titles[page][0]; $('#page-title').textContent = titles[page][1]; $('#page-description').textContent = titles[page][2]; $('#export').hidden = page !== 'overview'; history.replaceState(null, '', '#' + page) }
// data-* 속성으로 버튼과 페이지를 연결합니다. 이벤트 리스너가 사용자 입력을 받아 화면 상태를 갱신합니다.

// querySelectorAll은 여러 요소를 반환하고 forEach로 각 버튼에 클릭 처리기를 붙입니다.
// dataset.page는 HTML의 data-page 속성값입니다. data-goto는 본문 안 이동 버튼에 사용합니다.
document.querySelectorAll('[data-page]').forEach(b => b.addEventListener('click', () => navigate(b.dataset.page))); document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => navigate(b.dataset.goto)));

// 버튼을 누르면 channel 상태를 바꾸고 누른 버튼만 selected로 표시합니다.
// 그 뒤 render를 호출해야 상태 변경이 카드와 그래프에 반영됩니다.
document.querySelectorAll('[data-channel]').forEach(b => b.addEventListener('click', () => { channel = b.dataset.channel; document.querySelectorAll('[data-channel]').forEach(x => x.classList.toggle('selected', x === b)); render() }));

// 날짜 입력의 change 이벤트는 선택 값이 변경되어 확정될 때 발생합니다.
// render 함수를 직접 전달하므로 이벤트 발생 시 브라우저가 호출합니다.
for (const id of ['from', 'to']) $('#' + id).addEventListener('change', render);

// 필터를 전체 날짜/전체 채널로 되돌리고 버튼 선택 상태도 함께 복원합니다.
// 입력 값만 바꾸고 render를 빼면 기존 차트가 남으므로 마지막에 다시 그립니다.
$('#reset').addEventListener('click', () => { $('#from').value = dates[0]; $('#to').value = dates.at(-1); channel = 'all'; document.querySelectorAll('[data-channel]').forEach(b => b.classList.toggle('selected', b.dataset.channel === 'all')); render() });
// 선택 행을 CSV Blob으로 만들어 내려받습니다. BOM은 한글 인코딩 인식에 도움을 주고 object URL은 사용 후 해제합니다.

// 현재 필터 결과의 정해진 열만 CSV로 만듭니다.
// \ufeff는 UTF-8 BOM, \r\n은 행 구분이며 Blob으로 메모리 파일을 만듭니다.
// 가상 URL을 a.download로 내려받고 잠시 뒤 revokeObjectURL로 해제합니다.
// 현재 열은 날짜/정해진 채널/숫자이므로 단순 쉼표 연결이며 일반 텍스트 CSV의 따옴표 이스케이프 기능은 아닙니다.
$('#export').addEventListener('click', () => { const rows = filtered(); const columns = ['day', 'channel', 'orders', 'captured', 'refunded', 'net', 'delivered', 'late']; const csv = '\ufeff' + [columns.join(','), ...rows.map(r => columns.map(k => r[k]).join(','))].join('\r\n'); const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = 'shopscope-filtered.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) });
// 아래 상품·품질·실행/실험 표는 전체 스냅샷 기준입니다. 위 날짜/채널 필터는 render가 갱신하는 지표에만 적용됩니다.

// ---------------------------------------------------------
// 5. 전체 기간의 표와 실험 결과
// 여기부터는 최초 스냅샷 전체를 표시합니다.
// 상품 순위·품질·실행 기록·실험 결과는 위 기간/채널 필터로 재집계되지 않습니다.
// ---------------------------------------------------------
const icons = { 생활: '◒', 전자: '◉', 패션: '▱', 스포츠: '⌁' };

// 서버가 정렬해 둔 상위 상품 중 앞 5개만 slice로 선택합니다.
// map으로 행 HTML을 만들고 join(빈문자열)으로 붙입니다. 상품명 등 텍스트는 esc로 치환합니다.
$('#products').innerHTML = D.products.slice(0, 5).map(p => `<tr><td><div class="product-cell"><span class="product-icon">${icons[p.category] || '◇'}</span><div>${esc(p.title)}<small>${esc(p.product_id)}</small></div></div></td><td><span class="badge">${esc(p.category)}</span></td><td>${fmt(p.orders)}</td><td>${fmt(p.net)}원</td></tr>`).join('');

// 상태가 open인 품질 집계만 선택하여 count를 더합니다.
// 메뉴 옆 건수와 신뢰 지표에 같은 미해결 격리 수를 사용합니다.
const open = D.quality.filter(r => r.status === 'open').reduce((a, r) => a + n(r.count), 0); $('#quality-count').textContent = open;
$('#trust').innerHTML = [['격리된 데이터', fmt(open) + '건 검토 필요'], ['정산 금액 불일치', fmt(D.totals.mismatched) + '건'], ['처리 완료 파티션', fmt(D.batches.count) + '개'], ['최근 수신 파티션', D.batches.last_date]].map(([a, b]) => `<div class="trust-row"><span>${a}</span><strong>${esc(b)}</strong></div>`).join('');

// 기계가 저장한 오류 코드와 사람이 읽을 한글 설명을 연결합니다.
// 정의되지 않은 새 사유는 원래 코드를 표시하므로 화면이 빈 문자열이 되지 않습니다.
const reasons = { invalid_quantity: '수량 범위 오류', orphan_order: '연결할 주문 없음', conflicting_event_id: '같은 이벤트 ID · 다른 내용' };
$('#quality-table').innerHTML = D.quality.map(r => `<tr><td>${esc(r.source)}</td><td>${esc(reasons[r.reason] || r.reason)}</td><td><span class="badge ${r.status === 'open' ? 'warn' : 'ok'}">${r.status === 'open' ? '검토 필요' : '해결됨'}</span></td><td>${fmt(r.count)}</td></tr>`).join('');
$('#dimensions').innerHTML = [['완전성', '필수값 누락은 격리합니다. 선택 항목이 비어 있는 경우는 별도로 판단합니다.'], ['유일성', '고유 이벤트 ID와 내용 해시로 재전송과 충돌을 구분합니다.'], ['유효성', '수량·금액·시간대·이벤트 종류가 계약을 지키는지 검사합니다.'], ['정확성', '합성 생성기의 정답 합계와 원장 결과를 대조합니다.'], ['일관성', '환불액이 결제액을 넘지 않는지, 원장과 마트가 일치하는지 검사합니다.'], ['적시성', '발생 시각과 수신 시각의 차이로 늦게 도착한 이벤트를 확인합니다.']].map(([a, b]) => `<article class="quality-card"><b>${a}</b><p>${b}</p></article>`).join('');

// 실행 한 번마다 한 행을 만듭니다. skipped면 소요 시간 대신 이미 처리한 배치라고 표시합니다.
// rows_seen과 inserted가 다른 것은 중복/격리/건너뛰기 등이 있기 때문입니다.
$('#runs').innerHTML = D.runs.map(r => `<tr><td>${esc(r.business_date)}</td><td><span class="badge ${r.status === 'success' ? 'ok' : 'warn'}">${esc(r.status)}</span></td><td>${fmt(r.metrics.rows_seen)}</td><td>${fmt(r.metrics.inserted)}</td><td>${r.metrics.skipped ? '이미 처리한 배치' : n(r.metrics.elapsed_seconds).toFixed(2) + '초'}</td></tr>`).join('');
// 실험 결과가 있는 경우에만 막대를 만듭니다. 길이는 가장 큰 실행시간에 대한 상대 비율입니다.

// 실험 파일이 있을 때만 시간 막대를 채웁니다.
// summary의 median_seconds를 사용하므로 개별 시행 시간이 아닌 반복 중앙값입니다. 막대가 짧을수록 빠릅니다.
const bench = D.labs.benchmark; if (bench) { $('#bench-caption').textContent = fmt(bench.rows) + '행 · ' + bench.repeats + '회 중앙값 · 초'; const max = Math.max(...bench.summary.map(r => r.median_seconds)); $('#bench-bars').innerHTML = bench.summary.map(r => `<div class="bar-row"><div class="bar-label"><span>${esc(r.engine)}</span><b>${r.median_seconds.toFixed(3)}s</b></div><div class="bar-track"><div class="bar-fill" style="width:${r.median_seconds / max * 100}%"></div></div></div>`).join('') }
// 의미 검색은 미리 실행해 저장한 질의 예시를 선택하는 UI입니다. 새 문장을 모델에 보내는 실시간 검색은 아닙니다.

// ?.는 semantic이 없으면 오류 대신 undefined를 반환합니다.
// || []는 미실행일 때 빈 배열로 대체하여 선택 상자를 빈 상태로 만들 수 있게 합니다.
const examples = D.labs.semantic?.examples || []; $('#query').innerHTML = examples.map((r, i) => `<option value="${i}">${esc(r.query)}</option>`).join('') || '<option>의미 검색 실험을 먼저 실행하세요</option>';
// 선택한 예시의 상위 결과를 표시합니다. optional chaining과 빈 배열 기본값으로 미실행 실험도 처리합니다.

// 선택 상자의 value는 문자열이므로 n으로 숫자 인덱스로 바꿉니다.
// 저장된 해당 예시 hits 중 상위 4개를 보여 주며 모델을 새로 실행하지 않습니다.
function searchView() { const example = examples[n($('#query').value)]; $('#search-results').innerHTML = example ? example.hits.slice(0, 4).map((h, i) => `<div class="search-result"><div>${i + 1}. ${esc(h.title)}<small>${esc(h.category)} · ${esc(h.product_id)}</small></div><span>${h.similarity.toFixed(3)}</span></div>`).join('') : '<div class="empty">아직 검증 결과가 없습니다.</div>' } $('#query').addEventListener('change', searchView); searchView();
const storage = D.labs.storage; $('#lab-status').innerHTML = [['벡터 검색', D.labs.search ? 'pgvector HNSW·IVFFlat 회수율 실험 완료' : '미실행'], ['의미 검색', D.labs.semantic ? '로컬 MiniLM · 384차원 · 4개 질의 시연' : '미실행'], ['관계 탐색', storage?.neo4j ? 'Neo4j 상품 → 판매자 → 상품 2홉 조회 완료' : '미실행'], ['캐시', storage?.redis ? 'Redis miss/hit · TTL 만료 · 무효화 확인' : '미실행'], ['문서 DB', storage?.mongo.status === 'verified' ? 'MongoDB 조회 결과 대조 완료' : 'MongoDB 실행 미검증 · 환경 호환성 확인 필요'], ['실행 계획', D.labs.sql ? 'B-tree·GIN·표현식 인덱스 실측 완료' : '미실행']].map(([a, b]) => `<article class="quality-card"><b>${a}</b><p>${b}</p></article>`).join('');
$('#weeks').innerHTML = [['주문 한 건 따라가기', 'Python 함수·딕셔너리·JSON을 읽고 원천에서 DB까지 한 건을 추적합니다.'], ['SQL과 정산 정의', 'JOIN·GROUP BY·윈도우 함수로 수납액을 직접 검산합니다.'], ['실패와 재실행', '중복·롤백·늦은 환불 테스트를 실행하고 실패 원인을 설명합니다.'], ['대용량과 성능', '청크·자료형·Polars·Dask의 측정 결과를 비교합니다.'], ['저장소와 검색', 'JSONB·벡터·그래프·캐시의 역할과 한계를 구분합니다.'], ['설명과 시연', '데이터 조건 하나를 바꾸고 결과를 재검증한 뒤 5분 시연을 준비합니다.']].map(([a, b], i) => `<article class="week"><span>WEEK 0${i + 1}</span><h2>${a}</h2><p>${b}</p></article>`).join('');

// generated_at을 Date로 해석하고 Asia/Seoul 시간대로 표시합니다.
// 이 시각은 보고서 생성 시각이며 브라우저를 연 현재 시각이나 마지막 주문 시각이 아닙니다.
$('#updated').textContent = '스냅샷 ' + new Date(D.generated_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) + ' KST';
// 최초 집계와 URL 해시의 페이지를 표시합니다. 이후 hashchange 이벤트에도 메뉴 상태를 맞춥니다.
render(); navigate(location.hash.slice(1) || 'overview');


// 사용자가 URL의 #부분을 바꿨을 때도 페이지 상태를 맞춥니다.
// 처음 실행할 때는 위 render와 navigate가 초기 화면을 구성합니다.
window.addEventListener('hashchange', () => navigate(location.hash.slice(1) || 'overview'));
