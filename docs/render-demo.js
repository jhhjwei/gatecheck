// 生成 gatecheck 终端演示图（1040x700 PNG），输出取自 2026-10-31 本机实跑
ObjC.import('Cocoa');

function run(argv) {
const OUT = (argv && argv[0]) ? String(argv[0]) : './docs/demo.png';
const W = 1040, H = 700;
function color(r,g,b,a){ return $.NSColor.colorWithCalibratedRedGreenBlueAlpha(r/255,g/255,b/255,a===undefined?1:a); }
const C = {
  bg: color(13,17,23), bar: color(22,27,34), white: color(230,237,243), dim: color(125,138,151),
  green: color(63,185,80), red: color(248,81,73), orange: color(210,153,34), blue: color(88,166,255)
};
function font(size){ let f=$.NSFont.fontWithNameSize('Menlo-Regular', size); if(!f) f=$.NSFont.systemFontOfSize(size); return f; }
function attrs(col,size){ const d=$.NSMutableDictionary.alloc.init; d.setObjectForKey(font(size||17),$.NSFontAttributeName); d.setObjectForKey(col,$.NSForegroundColorAttributeName); return d; }
function strOf(s){ return $.NSString.alloc.initWithUTF8String(s); }
function text(s,x,top,col,size){
  const a=attrs(col,size); const sz=strOf(s).boundingRectWithSizeOptionsAttributes($.NSMakeSize(4000,4000),$.NSStringDrawingUsesLineFragmentOrigin,a).size;
  strOf(s).drawWithRectOptionsAttributes($.NSMakeRect(x,H-top-sz.height,sz.width+4,sz.height+2),$.NSStringDrawingUsesLineFragmentOrigin,a);
  return sz;
}
function rect(x,top,w,h,fill,r){ const p=$.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(x,H-top-h,w,h),r||0,r||0); fill.set; p.fill; }

const rep=$.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(null,W,H,8,4,true,false,$.NSCalibratedRGBColorSpace,0,0);
const ctx=$.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep);
$.NSGraphicsContext.saveGraphicsState; $.NSGraphicsContext.setCurrentContext(ctx);
C.bg.set; $.NSBezierPath.fillRect($.NSMakeRect(0,0,W,H));
rect(0,0,W,38,C.bar,0);
[color(255,95,86),color(255,189,46),color(39,201,63)].forEach((c,i)=>{ const p=$.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(18+i*24,H-26,13,13)); c.set; p.fill; });
text('gatecheck — demo', 100, 10, C.dim, 15);

const lines = [
  ['$ gatecheck lint drafts/ --platform toutiao', C.white],
  ['', C.white],
  ['⚠ G001 drafts/demo.md [toutiao] — 没有旁挂 meta，正文也找不到可点/可跑的锚点', C.orange],
  ['   fix: 补 <name>.meta.json 的 sources[]，或正文加入官方链接/可复现命令', C.dim],
  ['', C.white],
  ['✗ G002 drafts/demo.md:5 [toutiao] — 对外数量未核实：\'合集共九篇\'', C.red],
  ['   fix: 改成「本系列第 N 篇」这类不承诺总量的说法', C.dim],
  ['   或在 meta.verified_numbers 写前台实测数', C.dim],
  ['', C.white],
  ['ℹ G007 drafts/demo.md [toutiao] — 未记录 AI 声明状态', C.blue],
  ['结论：FAIL（1 fail / 1 warn / 1 info）', C.white],
  ['', C.white],
  ['$ gatecheck claim --platform toutiao --title "八月电费复盘"', C.white],
  ['✓ 已占锁：toutiao / 八月电费复盘', C.green],
  ['', C.white],
  ['$ gatecheck verify --platform toutiao --title "八月电费复盘" \\', C.white],
  ['    --evidence "列表里没有同标题行"', C.white],
  ['✓ 发前复查已记录（2026-10-31T08:04:06+08:00）', C.green],
  ['  现在 60 秒内点发布；超时重跑 verify', C.dim],
  ['', C.white],
  ['$ gatecheck publish --platform toutiao --title "八月电费复盘"', C.white],
  ['✗ 发前复查已过期 5 秒（窗口 60 秒），重新 verify 再点发布', C.red],
];
let top = 62;
lines.forEach(l => { if (l[0]) text(l[0], 24, top, l[1], 17); top += 28; });

$.NSGraphicsContext.restoreGraphicsState;
rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $()).writeToFileAtomically(OUT, true);
}
