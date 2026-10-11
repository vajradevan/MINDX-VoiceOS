import os
import re
import shutil
import subprocess
import urllib.parse
import webbrowser
import ctypes
from ctypes import wintypes
from pathlib import Path

import pyautogui
import pyttsx3
import speech_recognition as sr
from pycaw.pycaw import AudioUtilities


# Common Windows app names that can be launched directly.
WINDOWS_APP_ALIASES = {
    "notepad": "notepad.exe",
    "note pad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
}

speaker = pyttsx3.init()
recognizer = sr.Recognizer()


def say(message):
    print(f"VoiceOS: {message}")
    speaker.say(message)
    speaker.runAndWait()


def normalize_app_name(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def canonical_app_key(name):
    normalized = normalize_app_name(name)
    aliases = {
        "notepad": "notepad", "calculator": "calculator", "paint": "paint",
        "fileexplorer": "fileexplorer", "explorer": "fileexplorer",
        "microsoftstore": "microsoftstore", "windowsstore": "microsoftstore",
        "store": "microsoftstore", "edge": "microsoftedge",
        "microsoftedge": "microsoftedge", "chrome": "googlechrome",
        "googlechrome": "googlechrome", "firefox": "firefox",
    }
    return aliases.get(normalized, normalized)


def focus_existing_app(app_name, announce=True):
    """Bring a matching visible Windows app window forward instead of relaunching it."""
    target = normalize_app_name(app_name)
    if not target:
        return False

    user32 = ctypes.windll.user32
    found = []
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect_window(hwnd, _parameter):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        title = title_buffer.value.strip()
        normalized_title = normalize_app_name(title)
        # App names usually appear at the end of Windows window titles
        # (for example, "Untitled - Paint"). Suffix matching avoids treating
        # a browser page titled "Paint tutorial" as the Paint app itself.
        if normalized_title and normalized_title.endswith(target):
            found.append((hwnd, title))
            return False
        return True

    callback = enum_proc_type(collect_window)
    user32.EnumWindows(callback, 0)
    if not found:
        return False

    hwnd, title = found[0]
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE, including minimized windows.
    user32.SetForegroundWindow(hwnd)
    if announce:
        say(f"{app_name} is already open. Bringing it to the front.")
    return True


def find_start_menu_shortcut(app_name):
    """Find a matching shortcut in the Windows Start Menu programs folders."""
    target = normalize_app_name(app_name)
    roots = []

    for variable in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(variable)
        if base:
            roots.append(
                Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            )

    matches = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for shortcut in root.rglob("*.lnk"):
                candidate = normalize_app_name(shortcut.stem)
                if candidate == target:
                    matches.append((0, shortcut))
                elif target and target in candidate:
                    matches.append((10 + len(candidate) - len(target), shortcut))
                elif candidate and candidate in target:
                    matches.append((100 + len(target) - len(candidate), shortcut))
        except OSError:
            continue

    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def change_volume(target=None, amount=None, direction=0):
    """Set volume to a percentage, or change it by a number of points."""
    try:
        device = AudioUtilities.GetSpeakers()
        current = round(device.volume_percent)

        if target is not None:
            new_volume = max(0, min(100, target))
        else:
            new_volume = max(0, min(100, current + direction * amount))

        device.volume_percent = new_volume
        remember_undo("volume", f"volume change to {new_volume}%", previous=current)
        record_activity("DONE", f"Volume set to {new_volume}% (was {current}%).")
        say(f"Volume is now {new_volume} percent.")
    except Exception as error:
        say("I couldn't change the Windows volume.")
        print(f"Volume error: {error}")


apps_opened_this_task = set()
dictation_mode = False
last_opened_app = None
last_created_folder = None
pending_plan = None
plan_running = False
last_undo_action = None
activity_entries = []


def record_activity(status, message):
    """Keep a short, visible activity history in the VoiceOS terminal."""
    entry = f"[{status}] {message}"
    activity_entries.append(entry)
    del activity_entries[:-12]
    print(entry)


def remember_undo(kind, label, **details):
    """Remember only the latest action VoiceOS can safely reverse."""
    global last_undo_action
    last_undo_action = {"kind": kind, "label": label, **details}


def undo_last_action():
    """Reverse the last supported VoiceOS action, or send the app's Undo shortcut."""
    global last_undo_action, last_created_folder
    action = last_undo_action
    try:
        if action is None:
            pyautogui.hotkey("ctrl", "z")
            record_activity("UNDO", "Sent Ctrl+Z to the active app.")
            say("Undo sent to the active app.")
            return True

        kind = action["kind"]
        if kind == "volume":
            AudioUtilities.GetSpeakers().volume_percent = action["previous"]
        elif kind == "scroll":
            pyautogui.scroll(action["notches"])
        elif kind == "navigation":
            pyautogui.hotkey("alt", action["key"])
        elif kind == "folder":
            folder = Path(action["path"])
            if folder.exists():
                # rmdir only removes an empty folder; it never deletes its contents.
                folder.rmdir()
            if last_created_folder == folder:
                last_created_folder = None
        elif kind == "document":
            app_name = action.get("app")
            if app_name:
                focus_existing_app(app_name, announce=False)
            pyautogui.hotkey("ctrl", "z")
        elif kind == "none":
            last_undo_action = None
            say("The most recent action cannot be safely reversed.")
            record_activity("INFO", action["label"])
            return True
        else:
            raise ValueError("That action is not reversible.")

        label = action["label"]
        last_undo_action = None
        record_activity("UNDONE", label)
        say(f"Undid {label}.")
    except OSError as error:
        # In particular, a folder with files is never removed.
        say("I couldn't undo that safely. The folder may contain files.")
        print(f"Undo error: {error}")
    except Exception as error:
        say("I couldn't undo that action safely.")
        print(f"Undo error: {error}")
    return True


def documents_or_desktop_folder(location="documents"):
    """Return the user's OneDrive folder when present, otherwise the Windows profile folder."""
    one_drive = Path(os.environ.get("OneDrive", Path.home() / "OneDrive"))
    candidate = one_drive / ("Desktop" if location == "desktop" else "Documents")
    if candidate.exists():
        return candidate
    return Path.home() / ("Desktop" if location == "desktop" else "Documents")


def safe_windows_name(raw_name):
    """Keep spoken names as a single safe Windows file or folder name."""
    name = re.sub(r'[<>:"/\\|?*]', "_", raw_name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if name in ("", ".", "..") or name.upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        return None
    return name


def create_voice_folder(folder_name, location="documents"):
    """Create one named folder under Documents or Desktop; never execute shell text."""
    global last_created_folder
    safe_name = safe_windows_name(folder_name)
    if not safe_name:
        say("Please say a valid folder name, such as Project Notes.")
        return True
    folder = documents_or_desktop_folder(location) / safe_name
    try:
        already_existed = folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        last_created_folder = folder
        if already_existed:
            remember_undo("none", "existing folder", path=str(folder))
        else:
            remember_undo("folder", f"folder {safe_name}", path=str(folder))
        record_activity("DONE", f"Folder ready: {folder}")
        say(f"Folder {safe_name} is ready in your {location} folder.")
        print(f"Folder path: {folder}")
    except OSError as error:
        say("I couldn't create that folder.")
        print(f"Folder creation error: {error}")
    return True


def save_notepad_text(raw_name):
    """Save the open Notepad document with a spoken name in a known safe folder."""
    safe_name = safe_windows_name(raw_name)
    if not safe_name:
        say("Please say a valid file name, such as Meeting Notes.")
        return True

    if not focus_existing_app("Notepad", announce=False):
        say("Open Notepad and dictate your text first, then ask me to save it.")
        return True

    target_folder = last_created_folder
    if target_folder is None:
        target_folder = documents_or_desktop_folder("documents") / "VoiceOS Notes"
    try:
        target_folder.mkdir(parents=True, exist_ok=True)
        if safe_name.lower().endswith(".txt"):
            safe_name = safe_name[:-4]
        target = target_folder / f"{safe_name}.txt"
        suffix = 2
        while target.exists():
            target = target_folder / f"{safe_name} ({suffix}).txt"
            suffix += 1

        # Save As avoids accidentally replacing or typing over an existing note.
        pyautogui.hotkey("ctrl", "shift", "s")
        pyautogui.sleep(0.6)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.write(str(target), interval=0.002)
        pyautogui.press("enter")
        pyautogui.sleep(0.6)
        remember_undo("none", f"Saving {target.name} cannot be reversed by VoiceOS.")
        if target.exists():
            record_activity("DONE", f"Saved note: {target}")
            say(f"Saved your Notepad text as {target.name} in {target_folder.name}.")
            print(f"Saved file: {target}")
        else:
            say("I couldn't confirm the save. Check the Notepad Save As window.")
            print(f"Expected save path: {target}")
    except Exception as error:
        say("I couldn't save the Notepad text. Check the open Save As window.")
        print(f"Notepad save error: {error}")
    return True


def is_supported_plan_step(step):
    """Accept only action types that VoiceOS already knows how to perform."""
    patterns = (
        r"\b(?:open|launch)\s+|\bstart\s+(?!dictation\b)",
        r"\bstart dictation\b",
        r"\b(?:create|make)\s+(?:a\s+)?(?:new\s+)?folder\b",
        r"\bsave\b.*\b(?:as|to)\s+.+",
        r"\b(?:search(?:\s+the\s+web)?(?:\s+for)?|look\s+up|google)\s+",
        r"\b(?:volume|sound|audio|louder|quieter|softer)\b|\bturn it (?:up|down)\b",
        r"\b(?:go back|previous page|back one page|go forward|next page|forward one page)\b",
        r"\bscroll\b.*\b(?:up|down)\b",
    )
    return any(re.search(pattern, step) for pattern in patterns)

def explain_local_limit(command):
    say(
        "I don't have a local command for that yet. Try opening an app, searching the web, "
        "changing volume, scrolling, going back or forward, undoing, redoing, creating folders, saving Notepad notes, or dictating text."
    )
    print(f"Unrecognized local command: {command}")
    return True


def present_plan(steps):
    """Show an allowlisted multi-step plan and wait for an explicit spoken confirmation."""
    global pending_plan
    if len(steps) > 6:
        say("That plan has too many steps. Please split it into six steps or fewer.")
        record_activity("BLOCKED", "Plan exceeded the six-step safety limit.")
        return
    pending_plan = list(steps)
    print("\nPLAN PREVIEW — nothing has run yet:")
    for number, step in enumerate(pending_plan, start=1):
        print(f"  {number}. {step}")
    record_activity("WAITING", f"Plan with {len(pending_plan)} steps needs confirmation.")
    say(
        "Here is my plan: " + "; then ".join(pending_plan)
        + ". Say confirm plan to run it, or cancel plan."
    )


def run_confirmed_plan():
    """Run a previously previewed plan exactly once, showing progress for each step."""
    global pending_plan, plan_running
    steps = pending_plan or []
    pending_plan = None
    if not steps:
        say("There is no plan waiting for confirmation.")
        return True

    record_activity("STARTED", f"Running confirmed {len(steps)}-step plan.")
    say("Confirmed. I’m starting the plan.")
    plan_running = True
    try:
        for number, step in enumerate(steps, start=1):
            print(f"\nSTEP {number}/{len(steps)}: {step}")
            record_activity("RUNNING", f"Step {number}/{len(steps)}: {step}")
            if not handle_command(step):
                record_activity("STOPPED", "Plan stopped by VoiceOS.")
                return False
        record_activity("COMPLETE", "All confirmed plan steps finished.")
        say("All plan steps are complete.")
    except Exception as error:
        record_activity("FAILED", "Plan stopped after an error.")
        print(f"Plan error: {error}")
        say("The plan stopped because a step failed. Check the activity log.")
    finally:
        plan_running = False
    return True

def handle_command(command):
    global dictation_mode, last_opened_app, pending_plan
    command = command.lower().strip()
    print(f"You said: {command}")
    record_activity("HEARD", command[:100])

    if re.fullmatch(r"(?:stop|end|finish) dictation", command):
        if dictation_mode:
            dictation_mode = False
            say("Dictation stopped. Say save as followed by a file name to save your note.")
        else:
            say("Dictation is not running.")
        return True

    # Stop always takes priority, including while a task awaits approval.
    if re.fullmatch(
        r"(?:please\s+)?(?:stop(?:\s+(?:listening|voiceos))?|quit|exit)(?:\s+please)?",
        command,
    ):
        say("Stopping VoiceOS.")
        return False

    if pending_plan is not None:
        plan_reply = command.strip(" .!?")
        if re.fullmatch(r"(?:yes|confirm|confirm plan|run plan|go ahead|do it)", plan_reply):
            return run_confirmed_plan()
        if re.fullmatch(r"(?:no|cancel|cancel plan|never mind|nevermind)", plan_reply):
            step_count = len(pending_plan)
            pending_plan = None
            record_activity("CANCELLED", f"User cancelled the {step_count}-step plan.")
            say("Plan cancelled. Nothing was run.")
            return True
        say("I have a plan waiting. Say confirm plan to run it, or cancel plan.")
        return True

    # Saving is a command even during dictation, so it won't be typed into the note.
    save_match = re.fullmatch(
        r"(?:please\s+)?save\s+(?:(?:the|my|this)\s+)?"
        r"(?:(?:notepad|note|document|dictation|text)(?:\s+text)?|it|that)"
        r"(?:\s+(?:as|to)\s+(.+?))?[.!?]*",
        command,
    )
    save_as_match = re.fullmatch(r"(?:please\s+)?save\s+as\s+(.+?)[.!?]*", command)
    if save_match or save_as_match:
        if dictation_mode:
            dictation_mode = False
            say("Dictation stopped.")
        filename = save_as_match.group(1) if save_as_match else save_match.group(1)
        if not filename:
            say("Say save as followed by the file name, for example, save as meeting notes.")
            return True
        return save_notepad_text(filename)

    if dictation_mode:
        if re.fullmatch(r"(?:done|finish writing)", command):
            dictation_mode = False
            say("Dictation stopped. Say save as followed by a file name to save your note.")
            return True
        replacements = (
            (r"\bnew paragraph\b", "\n\n"),
            (r"\bnew line\b|\bnext line\b", "\n"),
            (r"\bcomma\b", ","),
            (r"\bquestion mark\b", "?"),
            (r"\bexclamation mark\b", "!"),
            (r"\b(full stop|period)\b", "."),
        )
        dictated_text = command
        for pattern, replacement in replacements:
            dictated_text = re.sub(pattern, replacement, dictated_text)
        try:
            if last_opened_app:
                focus_existing_app(last_opened_app, announce=False)
                pyautogui.sleep(0.2)
            lines = dictated_text.split("\n")
            for index, line in enumerate(lines):
                if line:
                    pyautogui.write(line, interval=0.01)
                if index < len(lines) - 1:
                    pyautogui.press("enter")
            if not dictated_text.endswith("\n"):
                pyautogui.write(" ", interval=0.01)
            remember_undo(
                "document", "the dictated text", app=last_opened_app
            )
            record_activity("DONE", f"Dictated: {dictated_text[:80]}")
            print(f"Dictated: {dictated_text}")
        except Exception as error:
            say("I couldn't type that phrase. Click inside the document and try again.")
            print(f"Dictation typing error: {error}")
        return True

    if re.fullmatch(r"(?:done|i am done|im done|that is all|that's all|finish task|end task)", command):
        last_opened_app = None
        apps_opened_this_task.clear()
        say("Task finished. I’m still listening for your next request.")
        return True

    if re.fullmatch(r"(?:start|begin) dictation", command):
        if last_opened_app:
            focus_existing_app(last_opened_app, announce=False)
        dictation_mode = True
        say("Dictation started. Speak naturally. Say stop dictation when finished, then save as and a file name.")
        return True

    folder_command = re.match(
        r"^(?:please\s+)?(?:create|make)\s+(?:a\s+)?(?:new\s+)?folder\b(.*)$",
        command,
    )
    if folder_command:
        folder_details = folder_command.group(1).strip(" .!?\t")
        location = "documents"
        location_match = re.search(r"\s+(?:on|in)\s+(?:my\s+|the\s+)?(desktop|documents?)$", folder_details)
        if location_match:
            location = "desktop" if location_match.group(1) == "desktop" else "documents"
            folder_details = folder_details[:location_match.start()].strip()
        folder_details = re.sub(r"^(?:named|called|for|and\s+name\s+it)\s+", "", folder_details)
        if not folder_details:
            say("Tell me the folder name, for example, create a folder called project notes.")
            return True
        return create_voice_folder(folder_details, location)

    # Undo and redo use standard shortcuts in the currently focused app.
    if re.search(r"\b(?:undo(?: that| the last action)?|take that back)\b", command):
        return undo_last_action()

    if re.search(r"\b(?:redo(?: that| the last action)?)\b", command):
        pyautogui.hotkey("ctrl", "y")
        say("Redo sent to the active app.")
        return True

    # Run known safe steps in order without opening a second copy of an app.
    steps = [part.strip() for part in re.split(
        r"\s+(?:and\s+then|then|and)\s+(?="
        r"(?:open|launch|start|search|look\s+up|google|set|change|click|double\s+click|type|enter|select|choose|fill|press|write|make|create|save|copy|paste|move|resize|close|"
        r"increase|raise|lower|decrease|turn|scroll|go|navigate)\b)",
        command,
    ) if part.strip()]
    if len(steps) > 1:
        if not all(is_supported_plan_step(step) for step in steps):
            return explain_local_limit(command)
        if not plan_running:
            present_plan(steps)
            return True

    if re.search(r"\b(help|commands|what can you do)\b", command):
        say(
            "I can open apps, search the web, set or change volume, scroll, "
            "go back or forward, undo, redo, create folders, save Notepad notes, and dictate text. "
            "I preview multi-step plans before running them. Say confirm plan to proceed, cancel plan to cancel, "
            "show activity to hear recent steps, or undo for a reversible last action. "
            "Say done to finish a task, or stop listening to exit."
        )
        return True

    if re.fullmatch(r"(?:show activity|show recent activity|what did you do|what have you done)", command):
        recent = activity_entries[-8:]
        if not recent:
            say("There is no recent activity yet.")
            return True
        print("\nRECENT ACTIVITY:")
        for entry in recent:
            print(f"  {entry}")
        say(f"I have {len(recent)} recent activity entries. They are listed in the terminal.")
        return True
        
    if re.search(
        r"\b(current volume|volume level|what is the volume|"
        r"what is my volume|how loud is it)\b",
        command,
    ):
        try:
            current = round(AudioUtilities.GetSpeakers().volume_percent)
            say(f"Volume is at {current} percent.")
        except Exception as error:
            say("I couldn't read the Windows volume.")
            print(f"Volume error: {error}")
        return True

    # Set a specific volume, for example: "set volume to 40 percent."
    volume_words = re.search(
        r"\b(volume|sound|audio|louder|quieter|softer)\b|"
        r"\bturn it (up|down)\b",
        command,
    )
    percent_match = re.search(r"\b(\d{1,3})\s*(percent|per cent|%)", command)

    if volume_words:
        if percent_match:
            number = int(percent_match.group(1))
            has_direction = re.search(
                r"\b(by|increase|raise|lower|decrease|up|down|"
                r"louder|quieter|higher|softer)\b",
                command,
            )

            # "to 40%" means set exactly; "up by 10%" means adjust.
            if has_direction and not re.search(r"\b(to|at)\b", command):
                direction_words = re.search(
                    r"\b(increase|raise|up|louder|higher)\b", command
                )
                direction = 1 if direction_words else -1
                change_volume(amount=number, direction=direction)
            else:
                change_volume(target=number)
            return True

        direction_up = re.search(
            r"\b(increase|raise|up|louder|higher)\b", command
        )
        direction_down = re.search(
            r"\b(lower|decrease|down|quieter|softer)\b", command
        )

        if direction_up or direction_down:
            direction = 1 if direction_up else -1
            change_volume(amount=5, direction=direction)
            return True

    # Search the words after "search for", "look up", or "Google".
    search_match = re.search(
        r"\b(?:search(?:\s+the\s+web)?(?:\s+for)?|look\s+up|google)"
        r"\s+(.+)",
        command,
    )
    if search_match:
        query = search_match.group(1).strip(" .?!")
        if query:
            url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
            webbrowser.open(url)
            remember_undo("none", "A web search cannot be reversed by VoiceOS.")
            record_activity("DONE", f"Searched the web for: {query}")
            say(f"Searching for {query}.")
            return True

    # Open an installed app, or search the web if no app shortcut is found.
    open_match = re.search(
        r"\b(?:open|launch|start)\s+(?:the\s+)?(.+?)\s*[.!?]*$",
        command,
    )

    if open_match:
        app_name = open_match.group(1).strip(" .!?")
        app_name = re.sub(r"\s+(?:please|now)$", "", app_name).strip()
        app_name = re.sub(r"^(?:a|an|the)\s+", "", app_name).strip()
        remember_undo("none", f"Opening {app_name} cannot be reversed by VoiceOS.")

        if app_name.lower() in ("browser", "web browser", "internet"):
            if "browser" in apps_opened_this_task:
                for browser_name in ("microsoft edge", "google chrome", "firefox", "brave", "opera"):
                    if focus_existing_app(browser_name):
                        return True
                say("Your browser is already open for this task.")
                return True
            webbrowser.open("https://www.google.com")
            apps_opened_this_task.add("browser")
            last_opened_app = None
            say("Opening your default browser.")
            return True

        normalized_app = normalize_app_name(app_name)
        app_key = canonical_app_key(app_name)

        if app_key in apps_opened_this_task:
            last_opened_app = app_name
            if not focus_existing_app(app_name):
                say(f"I already opened {app_name} for this task, so I won't launch another copy.")
            return True

        if focus_existing_app(app_name):
            apps_opened_this_task.add(app_key)
            last_opened_app = app_name
            return True

        # Windows shell apps may not have ordinary Start Menu shortcuts.
        if normalized_app == "recyclebin":
            try:
                subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"])
                apps_opened_this_task.add(app_key)
                last_opened_app = app_name
                say("Opening Recycle Bin.")
                return True
            except OSError as error:
                print(f"Could not open Recycle Bin: {error}")

        if normalized_app in ("microsoftstore", "windowsstore", "store"):
            try:
                os.startfile("ms-windows-store://home/")
                apps_opened_this_task.add(app_key)
                last_opened_app = app_name
                say("Opening Microsoft Store.")
                return True
            except OSError as error:
                print(f"Could not open Microsoft Store: {error}")

        executable = WINDOWS_APP_ALIASES.get(app_name.lower()) or shutil.which(app_name)
        if executable:
            try:
                subprocess.Popen([executable])
                apps_opened_this_task.add(app_key)
                last_opened_app = app_name
                say(f"Opening {app_name}.")
                return True
            except OSError as error:
                print(f"Could not launch executable: {error}")

        shortcut = find_start_menu_shortcut(app_name)
        if shortcut:
            try:
                os.startfile(str(shortcut))
                apps_opened_this_task.add(app_key)
                last_opened_app = app_name
                say(f"Opening {app_name}.")
                return True
            except OSError as error:
                print(f"Could not open app shortcut: {error}")

        query = urllib.parse.quote_plus(f"{app_name} official website")
        webbrowser.open(f"https://www.google.com/search?q={query}")
        say(f"I couldn't find {app_name} in the Start menu, so I searched for its website.")
        return True
    if re.search(r"\b(go back|previous page|back one page)\b", command):
        pyautogui.hotkey("alt", "left")
        remember_undo("navigation", "the previous-page action", key="right")
        record_activity("DONE", "Navigated back one page.")
        say("Going back.")
        return True

    if re.search(r"\b(go forward|next page|forward one page)\b", command):
        pyautogui.hotkey("alt", "right")
        remember_undo("navigation", "the next-page action", key="left")
        record_activity("DONE", "Navigated forward one page.")
        say("Going forward.")
        return True

    scroll_direction = None
    if re.search(r"\bscroll\b.*\bdown\b", command):
        scroll_direction = -1
    elif re.search(r"\bscroll\b.*\bup\b", command):
        scroll_direction = 1

    if scroll_direction:
        scroll_percent = re.search(r"\b(\d{1,3})\s*(percent|per cent|%)", command)

        if scroll_percent:
            percent = max(0, min(100, int(scroll_percent.group(1))))
            # Approximate a percentage of the visible screen.
            screen_height = pyautogui.size().height
            notches = round(screen_height * percent / 100 / 60)
            if percent == 0:
                say("Zero percent means no scrolling.")
                return True
            notches = max(1, min(100, notches))
        elif re.search(r"\b(little|bit)\b", command):
            notches = 500
        elif re.search(r"\b(lot|far)\b", command):
            notches = 500
        else:
            notches = 500

        pyautogui.scroll(scroll_direction * notches)
        remember_undo("scroll", "the scroll action", notches=-scroll_direction * notches)
        record_activity("DONE", f"Scrolled {'down' if scroll_direction < 0 else 'up'} {notches} notches.")
        say("Scrolling.")
        return True

    return explain_local_limit(command)


def main():
    say("VoiceOS is ready.")

    try:
        with sr.Microphone() as microphone:
            recognizer.adjust_for_ambient_noise(microphone, duration=1)

            while True:
                print("Listening...")
                try:
                    audio = recognizer.listen(
                        microphone, timeout=5, phrase_time_limit=8
                    )
                    # This sends recorded speech to Google's online
                    # speech recognition service.
                    command = recognizer.recognize_google(
                        audio, language="en-IN"
                    )
                    if not handle_command(command):
                        break
                except sr.WaitTimeoutError:
                    print("No speech heard. Try again.")
                except sr.UnknownValueError:
                    say("I couldn't understand that. Please try again.")
                except sr.RequestError as error:
                    say("Speech recognition couldn't connect.")
                    print(f"Recognition error: {error}")
                    break
    except OSError as error:
        print("Microphone problem:", error)
        print("Check your microphone connection and Windows microphone permissions.")


if __name__ == "__main__":
    main()
