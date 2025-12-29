from pathlib import Path
import pymupdf

def extract_plain_text(path: Path) -> str:
    doc = pymupdf.open(path)  # open a document
    result_text = []
    for page in doc:  # iterate the document pages
        # Exclude header (top 10%) and footer (bottom 10%) regions
        rect = page.rect
        header_height = rect.height * 0.10
        footer_height = rect.height * 0.10
        clip = pymupdf.Rect(0, header_height, rect.width, rect.height - footer_height)
        text = page.get_text(clip=clip)  # get text excluding header/footer regions
        result_text.append(text)
        result_text.append("\f")  # add page delimiter (form feed 0x0C)
    doc.close()
    return "".join(result_text)

if __name__ == "__main__":
    result = extract_plain_text(Path("cv/cv_daniel_heid_web.pdf"))
    print(result)