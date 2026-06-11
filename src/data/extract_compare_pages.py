import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
import fitz
from PIL import Image
import io

PDF = r'D:\\prj0617\\SKN29-3rd-4Team\\docs\\국립국어원_정겨운우리말.pdf'
OUT = r'D:\prj0617\SKN29-3rd-4Team\data\processed\ocr\compare'
os.makedirs(OUT, exist_ok=True)

doc = fitz.open(PDF)
for idx in [7, 8, 49, 99, 199, 339]:
    pix = doc[idx].get_pixmap(dpi=150)
    img = Image.open(io.BytesIO(pix.tobytes('png')))
    img.save(os.path.join(OUT, f'page_{idx+1}.png'))
    print(f'saved page_{idx+1}.png')
doc.close()