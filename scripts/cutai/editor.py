import argparse
import json
import re
import subprocess
from pathlib import Path

FILTERS={"none":"null","vivid":"eq=saturation=1.28:contrast=1.08","cinematic":"eq=saturation=.85:contrast=1.15:brightness=-.03","mono":"hue=s=0"}
EMPHASIS_TERMS={"absurdo","atenção","bomba","caramba","golaço","gol","histórico","impressionante","inacreditável","incrível","loucura","melhor","ninguém","polêmica","problema","segredo","sensacional","surpreendente","urgente","verdade"}

def _ass_time(seconds):
    seconds=max(0.0,float(seconds)); h=int(seconds//3600); m=int((seconds%3600)//60); s=seconds%60
    return f"{h}:{m:02d}:{s:05.2f}"

def _safe(text): return str(text).replace('\\',r'\\').replace('{',r'\{').replace('}',r'\}').replace('\n',r'\N').strip()
def _ass_color(value):
    value=value.strip().lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}',value): value='FFFFFF'
    r,g,b=value[0:2],value[2:4],value[4:6]
    return f"&H00{b}{g}{r}".upper()
def _emphasis(text): return bool(set(re.findall(r'[\wÀ-ÿ]+',text.lower())) & EMPHASIS_TERMS) or '!' in text

def _write_ass(path,captions,color,highlight,size,position,auto_emphasis):
    alignment={'top':8,'center':5,'bottom':2}.get(position,2); margin=90 if position!='center' else 20
    header=f'''[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nScaledBorderAndShadow: yes\nWrapStyle: 2\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,Arial,{size},{_ass_color(color)},&H00FFFFFF,&H00101010,&H78000000,-1,0,0,0,100,100,0,0,1,4,1,{alignment},120,120,{margin},1\nStyle: Emphasis,Arial,{min(96,size+8)},{_ass_color(highlight)},&H00FFFFFF,&H00000000,&H8A000000,-1,0,0,0,104,104,0,0,1,5,2,{alignment},120,120,{margin},1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'''
    events=[]
    for seg in captions.get('segments',[]):
        text=_safe(seg.get('text',''))
        if not text: continue
        style='Emphasis' if auto_emphasis and _emphasis(text) else 'Default'
        events.append(f"Dialogue: 0,{_ass_time(seg.get('start',0))},{_ass_time(seg.get('end',0))},{style},,0,0,0,,{text}")
    path.write_text(header+'\n'.join(events)+'\n',encoding='utf-8')

def render(source,output,resolution,filter_name,captions_path=None,caption_style='none',caption_color='#FFFFFF',highlight_color='#FFFF00',caption_size=62,caption_position='bottom',auto_emphasis=True):
    height=resolution if resolution in {720,1080,2160} else 1080; filters=[FILTERS.get(filter_name,'null'),f'scale=-2:{height}']; ass=None
    if caption_style!='none' and captions_path and captions_path.exists():
        captions=json.loads(captions_path.read_text(encoding='utf-8')); ass=output.with_suffix('.ass').resolve(); _write_ass(ass,captions,caption_color,highlight_color,max(28,min(96,caption_size)),caption_position,auto_emphasis); filters.append(f"ass=filename='{ass.as_posix()}'")
    preset='ultrafast' if height==2160 else 'veryfast'
    subprocess.run(['ffmpeg','-y','-i',str(source),'-vf',','.join(filters),'-c:v','libx264','-preset',preset,'-crf','20','-c:a','aac','-b:a','192k','-movflags','+faststart',str(output)],check=True)
    if ass: ass.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--resolution',type=int,default=1080); p.add_argument('--filter',default='none'); p.add_argument('--captions',type=Path); p.add_argument('--caption-style',default='none'); p.add_argument('--caption-color',default='#FFFFFF'); p.add_argument('--highlight-color',default='#FFFF00'); p.add_argument('--caption-size',type=int,default=62); p.add_argument('--caption-position',choices=['top','center','bottom'],default='bottom'); p.add_argument('--auto-emphasis',choices=['yes','no'],default='yes'); a=p.parse_args()
    render(a.source,a.output,a.resolution,a.filter,a.captions,a.caption_style,a.caption_color,a.highlight_color,a.caption_size,a.caption_position,a.auto_emphasis=='yes'); print(json.dumps({'output':str(a.output),'resolution':a.resolution,'filter':a.filter,'captions':a.caption_style!='none'}))
if __name__=='__main__': main()
