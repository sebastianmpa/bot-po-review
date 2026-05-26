import re

def clean_html_tags(text):
    """
    Limpia las etiquetas HTML <strong>, <i> y <em> (de apertura y cierre) de un texto.

    :param text: Texto en formato string que contiene etiquetas HTML.
    :return: Texto limpio sin las etiquetas especificadas.
    """
    # Expresión regular para eliminar las etiquetas <strong>, <i> y <em>
    clean_text = re.sub(r"</?(strong|i|em)>", "", text, flags=re.IGNORECASE)
    return clean_text

# Ejemplo de uso
if __name__ == "__main__":
    sample_text = "Este es un <strong>texto</strong> con <i>etiquetas</i> <em>HTML</em>."
    cleaned_text = clean_html_tags(sample_text)
    print("Texto original:", sample_text)
    print("Texto limpio:", cleaned_text)