import pyautogui

def move_mouse_and_click(x: int, y: int):
    """
    Mueve el mouse a las coordenadas especificadas y realiza un clic.

    :param x: Coordenada X a la que se moverá el mouse.
    :param y: Coordenada Y a la que se moverá el mouse.
    """
    pyautogui.moveTo(x, y)
    pyautogui.click()

if __name__ == "__main__":
    # Coordenadas de prueba
    x = 500
    y = 300
    print(f"Moviendo el mouse a las coordenadas ({x}, {y}) y haciendo clic...")
    move_mouse_and_click(x, y)
    print("Movimiento y clic completados.")