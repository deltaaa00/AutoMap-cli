#!/bin/bash


clear
echo "==================================="
echo "       SELECT OPTION (macOS)       "
echo " 1 - Add key      2 - Delete key   "
echo "==================================="

read -p "Enter Option: " user_input

if [[ "$user_input" == "1" ]]; then
    clear
    echo "Enter your Google AI Studio key:"
    read user_key
    
    if [[ -z "$user_key" ]]; then
        echo "Error: Key cannot be empty."
        exit 1
    fi

    echo "export GEMINI_API_KEY='$user_key'" >> ~/.zshrc
    
    export GEMINI_API_KEY="$user_key"

    echo "==================================="
    echo "  KEY ADDED TO .zshrc             "
    echo "  RESTART YOUR TERMINAL/IDE!      "
    echo "==================================="

elif [[ "$user_input" == "2" ]]; then
 
    sed -i '' '/export GEMINI_API_KEY=/d' ~/.zshrc
    echo "==================================="
    echo "          KEY DELETED              "
    echo "==================================="
else
    echo "Invalid option."
fi