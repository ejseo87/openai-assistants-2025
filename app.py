import json
import time
from openai import OpenAI
from langchain.utilities import WikipediaAPIWrapper
from langchain.document_loaders import WebBaseLoader
from langchain.tools import DuckDuckGoSearchResults
from langchain.tools import WikipediaQueryRun
from typing_extensions import override
from openai import AssistantEventHandler
import streamlit as st
import os

# Set user agent for web scraping
os.environ["USER_AGENT"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

CACHE_DIR = "temp"
FILE_DIR = os.path.join(CACHE_DIR, "text_files")
os.makedirs(FILE_DIR, exist_ok=True)


class AssistantEventHandler(AssistantEventHandler):

    message = ""

    @override
    def on_text_created(self, text) -> None:
        self.message_box = st.empty()

    def on_text_delta(self, delta, snapshot):
        self.message += delta.value
        self.message_box.markdown(self.message.replace("$", r"\$"))

    def on_event(self, event):

        if event.event == "thread.run.requires_action":
            submit_tool_outputs(event.data.id, event.data.thread_id)


st.set_page_config(
    page_title="Research Assistant",
    page_icon="🌐",
)

st.markdown(
    """
    # Research Assistant

    Input your research question and the assistant will provide a summary of the information.(테스트를 여러 번해서인지 duckduckgo 검색 차단 당한 것 같음. 실행 시간이 길어지면 차단된 것으로 추정하면 됨.)
    """
)


def wikipedia_search(inputs):
    query = inputs["query"]
    wiki = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper(top_k_results=5))
    return wiki.run(query)


def duckduckgo_search(inputs):
    try:
        query = inputs["query"]
        search = DuckDuckGoSearchResults()
        return search.run(query)
    except Exception as e:
        return f"Error performing DuckDuckGo search: {str(e)}"


def web_scraping(inputs):
    url = inputs["url"]
    loader = WebBaseLoader([url])
    docs = loader.load()
    text = "\n\n".join([doc.page_content for doc in docs])
    return text


def save_to_text(inputs):
    filename = inputs["filename"]
    content = inputs["content"]

    # Ensure filename has .txt extension
    if not filename.endswith('.txt'):
        filename += '.txt'

    # Create download button using Streamlit without saving to disk
    st.download_button(
        label=f"Download {filename}",
        data=content,
        file_name=filename,
        mime="text/plain"
    )

    return f"Content has been prepared for download as {filename}"


functions_map = {
    "wikipedia_search": wikipedia_search,
    "duckduckgo_search": duckduckgo_search,
    "web_scraping": web_scraping,
    "save_to_text": save_to_text,
}

functions = [
    {
        "type": "function",
        "function": {
            "name": "wikipedia_search",
            "description": "Use this tool to perform searches on Wikipedia. It takes a query as an argument. Example query: 'Artificial Intelligence'",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The query you will search for on Wikipedia",
                    }
                },
                "required": ["query"],
            },
        },

    },
    {
        "type": "function",
        "function": {
            "name": "duckduckgo_search",
            "description": "Use this tool to perform web searches using the DuckDuckGo search engine. It takes a query as an argument. Example query: 'Latest technology news'",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The query you will search for",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_scraping",
            "description": "If you found the website link in DuckDuckGo, Use this to get the content of the link for my research.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the website you want to scrape",
                    }
                },
                "required": ["url"],
            },
        },

    },
    {
        "type": "function",
        "function": {
            "name": "save_to_text",
            "description": "Use this tool to save the content as a .txt file and download it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "a name of the file you will save the research results",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content you will save to a file.",
                    }
                },
                "required": ["filename", "content"],
            },
        },

    }
]


def get_run(run_id, thread_id):
    return client.beta.threads.runs.retrieve(
        run_id=run_id,
        thread_id=thread_id,
    )


def wait_for_run_completion(thread_id):
    while True:
        runs = client.beta.threads.runs.list(thread_id=thread_id)
        active_runs = [run for run in runs if run.status in ['in_progress', 'queued']]
        if not active_runs:
            break
        time.sleep(1)


def send_message(thread_id, content):
    wait_for_run_completion(thread_id)
    return client.beta.threads.messages.create(
        thread_id=thread_id, role="user", content=content
    )


def get_messages(thread_id):
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    messages = list(messages)
    messages.reverse()
    return messages


def get_tool_outputs(run_id, thread_id):
    run = get_run(run_id, thread_id)
    outputs = []
    for action in run.required_action.submit_tool_outputs.tool_calls:
        action_id = action.id
        function = action.function
        print(f"Calling function: {function.name} with arg {function.arguments}")
        outputs.append(
            {
                "output": functions_map[function.name](json.loads(function.arguments)),
                "tool_call_id": action_id,
            }
        )
    return outputs


def submit_tool_outputs(run_id, thread_id):
    outputs = get_tool_outputs(run_id, thread_id)
    with client.beta.threads.runs.submit_tool_outputs_stream(
        run_id=run_id, thread_id=thread_id, tool_outputs=outputs, event_handler=AssistantEventHandler(),
    ) as stream:
        stream.until_done()


def send_chat_message(message, role):
    with st.chat_message(role):
        st.markdown(message)


def paint_chat_history(thread_id):
    messages = get_messages(thread_id)
    for message in messages:
        send_chat_message(
            message.content[0].text.value,
            message.role,
        )


with st.sidebar:
    st.markdown(
        "GitHub: [https://github.com/ejseo87/openai-assistants-2025](https://github.com/ejseo87/openai-assistants-2025)")
    st.markdown("---")
    openai_api_key = st.text_input("OpenAI API Key", type="password")

ASSISTANT_NAME = "Research Assistant"
if openai_api_key:
    client = OpenAI(
        api_key=openai_api_key,
    )
    if "assistant" not in st.session_state:
        assistants = client.beta.assistants.list(limit=5)
        print(assistants)
        if assistants:
            for a in assistants:
                print(a.name)
                if a.name == ASSISTANT_NAME:
                    assistant = client.beta.assistants.retrieve(a.id)
                    break
            else:
                assistant = client.beta.assistants.create(
                    name=ASSISTANT_NAME,
                    instructions="""
          You are a research expert.

          Your task is to search both Wikipedia and DuckDuckGo to gather comprehensive and accurate information about the question provided by the user.

          When you find a relevant website through DuckDuckGo, you must scrape the content from that website. When you scrape, you must use the function tool named web_scraping. Use this scraped content to thoroughly research and formulate a detailed answer to the question.

          After the above resarch, you shoud combine information from Wikipedia searches, DuckDuckGo searches, and any relevant websites you find. Ensure that the final answer is well-organized and detailed, and include citations with links (URLs) for all sources used.

          Finally your research must be saved to a .txt file and download it. You must use the function tool named save_to_text, and the content should match the detailed findings provided. Ensure that the final .txt file contains detailed information, all relevant sources, and citations.

          Do NOT make a download link and do NOT use sandbox.
          """,
                    model="gpt-4o-mini",
                    tools=functions,

                )

        thread = client.beta.threads.create()
        st.session_state["assistant"] = assistant
        st.session_state["thread"] = thread
        st.session_state["messages"] = []
    else:
        assistant = st.session_state.assistant
        thread = st.session_state.thread

    paint_chat_history(thread.id)
    query = st.chat_input("Enter your research question")
    if query:
        send_message(thread.id, query)
        send_chat_message(query, "user")
        with st.chat_message("assistant"):
            with client.beta.threads.runs.stream(
                thread_id=thread.id,
                assistant_id=assistant.id,
                event_handler=AssistantEventHandler(),
            ) as stream:
                stream.until_done()
