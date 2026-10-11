# MINDX VoiceOS

A Windows voice assistant prototype for local desktop actions using spoken English commands. It does not need an OpenAI API key.

## Features

- Opens apps found in Windows Start Menu shortcuts or on PATH; if an app is not found, searches for its website in the browser
- Opens the web browser and searches the web
- Sets Windows volume to a percentage or adjusts it up and down
- Scrolls up and down
- Navigates back and forward
- Reads the current volume and lists available commands
- Previews supported multi-step requests and waits for “confirm plan” before acting
- Shows a step-by-step activity log in the terminal and accepts “cancel plan”
- Reads out and prints recent actions when you say “show activity”
- Undoes the latest reversible VoiceOS action (volume, scrolling, navigation, dictation, or an empty folder it created)
- Brings an already-open matching app window forward instead of opening another copy
- Avoids opening duplicate copies of apps during the current task
- Dictates spoken words into the currently focused document after you say “start dictation”; say “stop dictation” to return to voice commands
- Creates a named folder in Documents or Desktop
- Saves the open Notepad document as a `.txt` file in the most recently created folder; otherwise uses `Documents/VoiceOS Notes`
- Sends undo and redo shortcuts to the active app

## Requirements

- Windows
- Python
- A working microphone and internet connection
- VS Code (recommended for editing)

Speech recognition uses Google's online service. Desktop commands run locally; this version does not send screenshots to an AI service.

## Setup

Open a terminal in this project folder and run:

    py -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install "SpeechRecognition[audio]" pyttsx3 pyautogui pycaw

## Run

    .\.venv\Scripts\python.exe voiceos.py

For a multi-step request, VoiceOS displays a numbered plan and waits. Say **“confirm plan”** (or “yes”) to run it, or **“cancel plan”** (or “no”) to do nothing. Say **“undo”** to reverse the last action VoiceOS can safely reverse. Undoing a folder only works while it is empty; VoiceOS never removes the folder's contents. If the last action is not reversible, VoiceOS says so. Say **“done”** to finish the current task and clear its short context while VoiceOS keeps listening. Say **“stop listening”** to exit.

## Example commands

- “Help”
- “Open Notepad”
- “Open Calculator”
- “Open Firefox” (or another installed app shown in the Start Menu)
- “Open browser”
- “Search the web for Python projects”
- “Set volume to 40 percent”
- “Increase volume by 10 percent”
- “What is my volume?”
- “Scroll down 25 percent”
- “Go back”
- “Go forward”
- “Undo”
- “Redo”
- “Open Notepad”, click in the page, then say “start dictation” and speak; say “stop dictation” when finished
- “Create a folder called project notes” (creates it in Documents)
- “Create a new folder named project files on the desktop”
- After dictating, say “save as meeting notes” or “save the Notepad text as meeting notes”
- Say “new paragraph”, “new line”, “comma”, or “period” while dictating for basic punctuation and line breaks
- “Open Notepad and then search the web for Python tutorials” (preview first, then confirm)
- “Open Calculator, then create a folder called calculations” (preview first; say “confirm plan” to run)
- “Cancel plan” (discard a waiting preview)
- “Undo” (reverse the last safely reversible action)
- “Show activity” (read and display recent VoiceOS actions)
- “Done” (finish the current task, keep VoiceOS listening, and clear recent task context)
- “Stop listening”

For scrolling, place the mouse pointer over the page first. Percentage-based scrolling is approximate and depends on the app and Windows scroll settings.

## Safety

VoiceOS uses only its built-in commands. Supported multi-step requests are previewed and require a spoken confirmation before running. Dictation types into the currently focused app, so click where you want the text before starting. Folder creation is limited to Documents or Desktop. Saving uses a new filename to avoid replacing existing notes. Undo is limited to known reversible actions; it never deletes folder contents. VoiceOS does not run terminal commands, install software, or send screenshots to an AI service.
