' Smart Layer - launch a .cmd with no visible console window and wait for it to finish.
' Used by the scheduled tasks when they run as a signed-in user (Interactive), so the 5-minute
' live sync and the daily run do not flash a black window. Argument 0 is the .cmd to run.
Set sh = CreateObject("WScript.Shell")
sh.Run """" & WScript.Arguments(0) & """", 0, True
