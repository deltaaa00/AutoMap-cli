@echo off


:menu
color 07
cls
echo ===================================
echo SELECT OPTION
echo 1 - Add key     2 - Delete key
echo ===================================

set /p user_input="Enter Option: "

if %user_input% == 1 goto key_adding
if %user_input% == 2 goto key_deleting


color 0C
echo.
echo ERROR: Please enter a correct option (1 or 2).
pause
goto menu


:key_adding
cls
set /p user_key="Enter your Google AI Studio key: "
setx GEMINI_API_KEY "%user_key%"
cls
echo ===================================
echo KEY ADDED ^| NOW YOU CAN LAUNCH AM 
echo ===================================
pause
exit

:key_deleting
reg delete "HKCU\Environment" /v GEMINI_API_KEY
cls
color 0A
echo ===================================
echo 		KEY DELETED  
echo ===================================
pause
exit