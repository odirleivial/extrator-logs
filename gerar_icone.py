"""Gera icon.ico para o Backoffice Equipe QA."""
from PIL import Image, ImageDraw, ImageFont
import math, os

COR_FUNDO  = (26,  58,  92,  255)   # #1a3a5c
COR_RODAPE = (15,  37,  64,  255)   # #0f2540
COR_AZUL   = (74, 144, 226, 255)    # #4a90e2
COR_CIANO  = (79, 195, 247, 255)    # #4fc3f7

def rect_rnd(draw, x0, y0, x1, y1, r, cor):
    r = max(0, int(r))
    if x1 - x0 < 2*r: r = (x1 - x0) // 2
    if y1 - y0 < 2*r: r = (y1 - y0) // 2
    if r <= 0:
        draw.rectangle([x0, y0, x1, y1], fill=cor)
        return
    draw.rectangle([x0+r, y0, x1-r, y1], fill=cor)
    draw.rectangle([x0, y0+r, x1, y1-r], fill=cor)
    draw.ellipse([x0,      y0,      x0+2*r, y0+2*r], fill=cor)
    draw.ellipse([x1-2*r,  y0,      x1,     y0+2*r], fill=cor)
    draw.ellipse([x0,      y1-2*r,  x0+2*r, y1],     fill=cor)
    draw.ellipse([x1-2*r,  y1-2*r,  x1,     y1],     fill=cor)

def desenhar_icone(size):
    s   = size
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    raio = max(2, int(s * 0.18))

    # Fundo principal
    rect_rnd(d, 0, 0, s-1, s-1, raio, COR_FUNDO)

    # Faixa inferior (só para tamanhos >= 32)
    if s >= 32:
        faixa_y = int(s * 0.72)
        # corpo da faixa
        rect_rnd(d, 0, faixa_y, s-1, s-1, raio, COR_RODAPE)
        # preenche o gap entre o arredondado e o corpo principal
        d.rectangle([0, faixa_y, s-1, faixa_y + raio], fill=COR_RODAPE)

    # Lupa
    cx     = int(s * 0.44)
    cy     = int(s * 0.43)
    r_ext  = max(3, int(s * 0.21))
    esp    = max(1, int(s * 0.06))

    # Aro externo
    d.ellipse([cx-r_ext, cy-r_ext, cx+r_ext, cy+r_ext], outline=COR_AZUL, width=esp)

    # Interior semitransparente
    r_int = max(1, r_ext - esp)
    ov = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.ellipse([cx-r_int, cy-r_int, cx+r_int, cy+r_int], fill=(74, 144, 226, 46))
    img = Image.alpha_composite(img, ov)
    d   = ImageDraw.Draw(img)

    # Checkmark (só >= 32)
    if s >= 32:
        ck  = r_int * 0.55
        p1  = (cx - ck*0.5,  cy)
        p2  = (cx,            cy + ck*0.5)
        p3  = (cx + ck*0.9,  cy - ck*0.7)
        lw  = max(1, int(s * 0.05))
        d.line([p1, p2], fill=COR_CIANO, width=lw)
        d.line([p2, p3], fill=COR_CIANO, width=lw)

    # Cabo da lupa
    ang      = math.radians(45)
    ini_x    = cx + int((r_ext - esp//2) * math.cos(ang))
    ini_y    = cy + int((r_ext - esp//2) * math.sin(ang))
    cabo_len = max(2, int(s * 0.22))
    fim_x    = ini_x + int(cabo_len * math.cos(ang))
    fim_y    = ini_y + int(cabo_len * math.sin(ang))
    lw_cabo  = max(2, int(s * 0.065))
    d.line([(ini_x, ini_y), (fim_x, fim_y)], fill=COR_AZUL,  width=lw_cabo)
    d.line([(ini_x, ini_y), (fim_x, fim_y)], fill=COR_CIANO, width=max(1, lw_cabo//3))

    # Texto "BEQ" (só >= 48)
    if s >= 48:
        fs  = max(8, int(s * 0.135))
        txt = "BEQ"
        try:
            fnt = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", fs)
        except Exception:
            fnt = ImageFont.load_default()
        bbox = d.textbbox((0, 0), txt, font=fnt)
        tw   = bbox[2] - bbox[0]
        th   = bbox[3] - bbox[1]
        ty   = int(s * 0.83) - th // 2
        d.text(((s - tw) // 2, ty), txt, font=fnt, fill=COR_AZUL)

    return img


if __name__ == '__main__':
    tamanhos = [256, 128, 64, 48, 32, 16]
    frames   = [desenhar_icone(t) for t in tamanhos]

    saida = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    frames[0].save(
        saida,
        format='ICO',
        sizes=[(t, t) for t in tamanhos],
        append_images=frames[1:]
    )
    print(f"Ícone gerado: {saida}")
    print(f"Tamanhos incluídos: {tamanhos}")
