# termux-agent
Ultra-lightweight and compact AI agent designed for low-end Android devices.

## why? 
The project grew out of a personal need; I really just wanted a simple agent capable of creating files that support the Groq API. That wasn't what I found in my searches, so—as that philosopher whose name I've forgotten would say "be the change you want to see in the world."

## how use 
Clone the repository or copy the contents of agent.py 

### export scope variables
```.env
export AGENT_API_KEY="your_groq_api_key"
export AGENT_API_URL="https://api.groq.com/openai/v1/chat/completions"
export AGENT_MODEL="openai/gpt-oss-20b"
export AGENT_WORKDIR="~"
export AGENT_MAX_TOOL_CALLS="12"
```

## run the agent 
use the command 
```bash
python agent.py "your_prompt"
```
