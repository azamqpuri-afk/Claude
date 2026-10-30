' Smart Layer - launch a .cmd with no visible console window and wait for it to finish.
' Used by the scheduled tasks when they run as a signed-in user (Interactive), so the 5-minute
' live sync and the daily run do not flash a black window. Argument 0 is the .cmd to run.
Set sh = CreateObject("WScript.Shell")
' "cmd /c" so the .cmd's exit code is returned; WScript.Quit passes it to Task Scheduler (0 = success).
WScript.Quit sh.Run("cmd /c """ & WScript.Arguments(0) & """", 0, True)
