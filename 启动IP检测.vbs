Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
base = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base
shell.Run Chr(34) & "G:\ANAconda123\pythonw.exe" & Chr(34) & " " & Chr(34) & base & "\app.py" & Chr(34), 0, False
