import customtkinter as ctk
from gui import DeepSeekGUI

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = DeepSeekGUI()
    app.mainloop()