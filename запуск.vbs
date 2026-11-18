Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "pythonw.exe was not found: " & pythonw, vbCritical, "photofordocs"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
' 1 = normal window. 0 hides the program, so a double-click looks like nothing happened.
shell.Run """" & pythonw & """ -m photofordocs", 1, False
