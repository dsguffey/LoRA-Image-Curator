Option Explicit

' Known issue:
' The hidden launcher has not remained open reliably on the current system.
' Use "Run LoRA Image Curator - Diagnostic.bat" until BUGS.md is resolved.

Dim fileSystem, shell, scriptDirectory, pythonWindowed, applicationScript, command

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonWindowed = scriptDirectory & "\venv\Scripts\pythonw.exe"
applicationScript = scriptDirectory & "\app.py"

If Not fileSystem.FileExists(pythonWindowed) Then
    MsgBox "LoRA Image Curator could not find:" & vbCrLf & vbCrLf & _
           pythonWindowed & vbCrLf & vbCrLf & _
           "Place these files beside the existing venv directory.", _
           vbCritical, "LoRA Image Curator"
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDirectory
command = Chr(34) & pythonWindowed & Chr(34) & " " & Chr(34) & applicationScript & Chr(34)
shell.Run command, 0, False
