from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'Quasar_poster'
OUT.mkdir(exist_ok=True)
ppt_path = OUT / 'Quasar_홍보포스터.pptx'
png_path = OUT / 'Quasar_홍보포스터_미리보기.png'
if ppt_path.exists() or png_path.exists():
    raise FileExistsError('Output already exists; refusing to overwrite.')
W, H = 1120, 1584
SCALE = 2
BG = '#090E20'
WHITE = '#F4F7FF'
MUTED = '#ACB9D3'
CYAN = '#57E4ED'
PURPLE = '#A999FF'
CARD = '#121C34'
LINE = '#283650'
prs = Presentation()
prs.slide_width = Inches(11.69)
prs.slide_height = Inches(16.533)
slide = prs.slides.add_slide(prs.slide_layouts[6])
slide.background.fill.solid()
slide.background.fill.fore_color.rgb = RGBColor.from_string(BG[1:])
image = Image.new('RGB', (W*SCALE, H*SCALE), BG)
draw = ImageDraw.Draw(image)
fontdir = Path('C:/Windows/Fonts')
regular = fontdir / 'malgun.ttf'
boldfont = fontdir / 'malgunbd.ttf'
assert regular.exists() and boldfont.exists(), 'Korean font not found'

def u(v):
    return int(prs.slide_width * v / W)

def rgb(c):
    return RGBColor.from_string(c.lstrip('#'))

def box(x,y,w,h,fill,stroke=None,r=0,ellipse=False):
    kind = MSO_SHAPE.OVAL if ellipse else (MSO_SHAPE.ROUNDED_RECTANGLE if r else MSO_SHAPE.RECTANGLE)
    sh = slide.shapes.add_shape(kind,u(x),u(y),u(w),u(h))
    if r and not ellipse:
        sh.adjustments[0] = min(r/min(w,h), 0.5)
    if fill:
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fill)
    else:
        sh.fill.background()
    if stroke:
        sh.line.color.rgb = rgb(stroke); sh.line.width = Pt(1)
    else:
        sh.line.fill.background()
    bounds = (int(x*SCALE),int(y*SCALE),int((x+w)*SCALE),int((y+h)*SCALE))
    if ellipse:
        draw.ellipse(bounds,fill=fill,outline=stroke,width=2)
    elif r:
        draw.rounded_rectangle(bounds,radius=int(r*SCALE),fill=fill,outline=stroke,width=2)
    else:
        draw.rectangle(bounds,fill=fill,outline=stroke,width=2)
    return sh

def text(x,y,w,h,content,size=22,color=WHITE,bold=False):
    font = ImageFont.truetype(str(boldfont if bold else regular),int(size*SCALE))
    for line in content.split('\n'):
        if draw.textlength(line,font=font) > w*SCALE:
            raise ValueError('Text exceeds width: '+line)
    sh = slide.shapes.add_textbox(u(x),u(y),u(w),u(h))
    tf = sh.text_frame
    tf.clear(); tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.TOP
    for i,line in enumerate(content.split('\n')):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.name = '맑은 고딕'
        p.font.size = Pt(size * 11.69 * 72 / W)
        p.font.bold = bold
        p.font.color.rgb = rgb(color)
        p.space_before = p.space_after = Pt(0)
        p.line_spacing = 1.25
        draw.text((x*SCALE,(y+i*size*1.25)*SCALE),line,font=font,fill=color,anchor='lt')
    return sh

# Restrained background accents and editable quasar mark.
box(0,0,W,10,CYAN)
box(72,76,42,42,None,CYAN,ellipse=True)
box(84,88,18,18,CYAN,ellipse=True)
text(130,72,350,65,'Quasar',46,WHITE,True)
box(733,82,315,38,'#17223D',r=19)
text(754,91,280,27,'PERSONAL AI AGENT',17,CYAN,True)
text(72,179,740,205,'대답을 넘어,\n실행하는 AI.',76,WHITE,True)
text(75,393,940,78,'개발 · 정보 검색 · 업무 관리까지\n연결된 도구로 실제 작업을 수행하는 개인 AI 에이전트',25,MUTED)

# Orbital illustration: native editable PowerPoint shapes.
cx, cy = 900, 285
box(cx-120,cy-120,240,240,'#101A36',ellipse=True)
box(cx-99,cy-99,198,198,None,'#3A4272',ellipse=True)
box(cx-122,cy-42,244,84,None,CYAN,ellipse=True)
box(cx-42,cy-122,84,244,None,'#7C70BD',ellipse=True)
box(cx-53,cy-53,106,106,'#252D59',ellipse=True)
box(cx-32,cy-32,64,64,PURPLE,ellipse=True)
box(cx-16,cy-16,32,32,WHITE,ellipse=True)
box(cx+98,cy-29,13,13,CYAN,ellipse=True)
box(cx-39,cy-112,10,10,PURPLE,ellipse=True)

# A clearly labelled example, not a fabricated execution result.
box(72,520,976,205,CARD,LINE,22)
text(100,542,500,25,'하나의 채팅창에서 이어지는 작업',18,CYAN,True)
text(100,585,920,49,'“코드를 점검하고, 자료를 찾아 할 일로 정리해 줘.”',28,WHITE,True)
for x,width,label in [(100,172,'01  요청 이해'),(294,172,'02  도구 실행'),(488,194,'03  결과 확인')]:
    box(x,650,width,42,'#20304D',r=10)
    text(x+14,660,width-22,26,label,18,WHITE,True)
text(718,661,300,26,'작업 흐름 예시',17,MUTED)

text(72,766,900,45,'당신의 작업을 연결하는 4가지 능력',30,WHITE,True)

def card(x,y,number,title,tag,body,accent):
    box(x,y,476,209,CARD,LINE,18)
    box(x+22,y+24,42,36,'#23304E',r=8)
    text(x+30,y+30,30,25,number,18,accent,True)
    text(x+78,y+27,360,42,title,28,WHITE,True)
    text(x+24,y+83,430,28,tag,17,accent,True)
    text(x+24,y+122,434,78,body,20,MUTED)

card(72,831,'01','개발의 흐름을 더 빠르게','VS CODE + WORKSPACE',
     '편집 파일·진단 정보 기반 코드 지원\n파일 읽기·수정, Python 문법 검사·실행\n문서 색인과 질문에 맞는 내용 검색',CYAN)
card(572,831,'02','검색부터 브라우저 작업까지','CHROME + GMAIL',
     'Chrome 탭 확인, 웹 검색·이동·클릭·입력\nGmail 검색과 메일 임시저장\n메일 전송은 명확한 사용자 요청 시에만',PURPLE)
card(72,1064,'03','일상 업무도 한곳에서','TODO + TIME + CALENDAR',
     '할 일 관리와 현재 시간 확인\n타이머·알람 설정\n캘린더 일정 파일 생성',CYAN)
card(572,1064,'04','내 방식대로 연결하고 확장','VOICE + MODELS + MCP',
     '음성 입력·답변 재생, 입력 마이크 선택\n로컬 음성 인식, Ollama·OpenAI 선택\nMCP 연결로 필요한 외부 도구 추가',PURPLE)

box(72,1305,976,111,'#102A36',r=16)
box(94,1329,5,62,CYAN,r=2)
text(116,1325,900,33,'실행 과정은 투명하게, 파일 수정은 신중하게',23,WHITE,True)
text(116,1368,900,30,'진행 상황·도구 결과 확인  |  작업 폴더 범위 확인  |  저장 전 파일 변경 여부 검사',18,MUTED)
text(72,1460,900,50,'이제, 질문을 작업으로 바꾸세요.',36,WHITE,True)
text(74,1523,850,25,'Quasar  /  하나의 채팅창, 연결되는 가능성',18,CYAN)
text(915,1526,140,24,'AI THAT ACTS',14,MUTED,True)

prs.core_properties.title = 'Quasar | 대답을 넘어, 실행하는 AI'
prs.core_properties.subject = '개인 AI 에이전트 Quasar 홍보 포스터'
prs.core_properties.author = 'Quasar'
prs.core_properties.keywords = 'Quasar, AI Agent, VS Code, Chrome, MCP'
prs.save(str(ppt_path))
image.save(str(png_path))

# Reopen the produced file and validate the package and editable content.
import zipfile
with zipfile.ZipFile(ppt_path) as package:
    assert package.testzip() is None
check = Presentation(str(ppt_path))
assert len(check.slides) == 1
texts = [s.text for s in check.slides[0].shapes if s.has_text_frame]
assert any('실행하는 AI' in t for t in texts)
for s in check.slides[0].shapes:
    assert s.left >= 0 and s.top >= 0
    assert s.left + s.width <= check.slide_width + u(1)
    assert s.top + s.height <= check.slide_height + u(1)
print('VERIFIED:', ppt_path)
print('PREVIEW:', png_path)
print('SLIDES:',len(check.slides),'EDITABLE_TEXT_BOXES:',len(texts),'BYTES:',ppt_path.stat().st_size)
