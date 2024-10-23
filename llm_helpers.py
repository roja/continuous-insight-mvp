"""
Helper functions for interacting with OpenAI/LLM services.
Handles text generation, analysis, and processing using AI models.
"""

import json
import base64
from typing import List, Tuple, Optional
import time

from openai import OpenAI
from db_models import CriteriaDB

# Initialize OpenAI client
openai_client = None


def init_openai_client(api_key: str):
    """Initialize the OpenAI client with the provided API key."""
    global openai_client
    openai_client = OpenAI(api_key=api_key)


def analyze_image(image_path: str) -> Optional[str]:
    """
    Analyze image content using OpenAI's API.
    Returns a description of the image content or None if irrelevant/error.
    """
    max_retries = 3
    retry_delay = 5  # seconds

    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")

    functions = [
        {
            "name": "describe_image",
            "description": "Describes the content of an image relevant to a technical and product audit",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "A detailed description of the image content, or 'irrelevant' if the image is not relevant to the audit",
                    }
                },
                "required": ["description"],
            },
        }
    ]

    for attempt in range(max_retries):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert technical and product auditor. You reply in british english. Your task is to analyse images for a technical and product audit process.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze this image for our technical and product audit. If it's irrelevant (like a logo or unrelated picture), respond with 'irrelevant'. Otherwise, provide a detailed description of the content, especially if it's a system screenshot, architecture diagram, process chart, or documentation. Focus on factual information without assessing maturity.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                },
                            },
                        ],
                    },
                ],
                functions=functions,
                function_call={"name": "describe_image"},
            )

            function_call = response.choices[0].message.function_call
            if function_call and function_call.name == "describe_image":
                description = eval(function_call.arguments)["description"]
                return description if description != "irrelevant" else None

        except Exception as e:
            print(f"Error analyzing image, attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to analyze image after {max_retries} attempts.")
                return None

    return None


def transcribe_audio_chunk(file_path: str) -> Optional[str]:
    """Transcribe a single audio file using OpenAI's Whisper API."""
    try:
        with open(file_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1", file=audio_file
            )
        return transcript.text
    except Exception as e:
        print(f"Error transcribing audio: {str(e)}")
        return None


def extract_evidence_from_text(
    content: str, criteria: CriteriaDB
) -> Tuple[str, List[str]]:
    """Extract relevant evidence from text content based on criteria using LLM."""
    system_prompt = (
        "You are an expert auditor tasked with extracting relevant evidence from documents based on specific criteria. Always use british english. "
        "Given the criteria and a document, extract and return a summary and relevant quotes or references from the document that pertain to the criteria. "
        "The summary should be a concise overview of the relevant content, and the quotes should be sentences to paragraphs in length that help an expert auditor assess the maturity of the organization's technology and product functions. "
        "Provide the output in a structured JSON format as per the function schema."
    )

    maturity_definitions_str = (
        "\n".join(
            [
                f"{level}: {desc}"
                for level, desc in criteria.maturity_definitions.items()
            ]
        )
        if isinstance(criteria.maturity_definitions, dict)
        else str(criteria.maturity_definitions)
    )

    user_message = (
        f"Criteria:\nTitle: {criteria.title}\nDescription: {criteria.description}\n"
        f"Maturity Definitions:\n{maturity_definitions_str}\n\n"
        f"Source Document Content:\n{content}"
    )

    functions = [
        {
            "name": "extract_relevant_content",
            "description": "Extracts relevant content from the document that pertains to the criteria. Always use british english.",
            "parameters": {
                "type": "object",
                "properties": {
                    "has_relevant_content": {
                        "type": "boolean",
                        "description": "True if the document has relevant content for the criteria, false otherwise.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "A concise summary of the relevant content within the document pertaining to the criteria. Should be empty if 'has_relevant_content' is false.",
                    },
                    "quotes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of highly relevant exact quotes from the source. Each one would help an expert auditor to assess the maturity of a company's tech/product function. Should be empty if 'has_relevant_content' is false.",
                    },
                },
                "required": ["has_relevant_content"],
            },
        }
    ]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            functions=functions,
            function_call={"name": "extract_relevant_content"},
            max_tokens=2000,
        )

        function_call = response.choices[0].message.function_call
        if function_call and function_call.name == "extract_relevant_content":
            arguments = json.loads(function_call.arguments)
            has_relevant_content = arguments.get("has_relevant_content", False)
            if has_relevant_content:
                return arguments.get("summary", ""), arguments.get("quotes", [])

        return "", []

    except Exception as e:
        print(f"Error extracting evidence: {str(e)}")
        return "", []


def generate_questions_using_llm(
    criteria: CriteriaDB, evidence_content: str
) -> List[str]:
    """Generate questions based on criteria and evidence using LLM."""
    system_prompt = (
        "You are an expert auditor tasked with assessing the maturity of an organization's technical and product departments based on specific criteria and available evidence. Always use british english. "
        "Your goal is to determine whether the current evidence is sufficient to assess the maturity level. "
        "If the evidence is sufficient, generate additional questions to dig deeper into the most relevant areas of the current evidence. "
        "If the evidence is not sufficient, generate questions that would fill the gaps in knowledge needed for maturity assessment."
    )

    maturity_definitions_str = (
        "\n".join(
            [
                f"{level}: {desc}"
                for level, desc in criteria.maturity_definitions.items()
            ]
        )
        if isinstance(criteria.maturity_definitions, dict)
        else str(criteria.maturity_definitions)
    )

    user_message = (
        f"Criteria:\nTitle: {criteria.title}\nDescription: {criteria.description}\n"
        f"Maturity Definitions:\n{maturity_definitions_str}\n\n"
        f"Available Evidence:\n{evidence_content}"
    )

    functions = [
        {
            "name": "generate_questions",
            "description": "Generates questions to help assess the maturity level based on the criteria and available evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_sufficient": {
                        "type": "boolean",
                        "description": "True if the current evidence is sufficient to assess the maturity level, False otherwise.",
                    },
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of questions to either dig deeper into existing evidence or fill knowledge gaps.",
                    },
                },
                "required": ["evidence_sufficient", "questions"],
            },
        }
    ]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            functions=functions,
            function_call={"name": "generate_questions"},
            max_tokens=2000,
            temperature=0.7,
        )

        function_call = response.choices[0].message.function_call
        if function_call and function_call.name == "generate_questions":
            arguments = json.loads(function_call.arguments)
            return arguments.get("questions", [])

        return []

    except Exception as e:
        print(f"Error generating questions: {str(e)}")
        return []


def analyze_company_evidence(raw_evidence: str) -> dict:
    """Analyze company evidence using LLM and return structured information."""
    company_info_function = {
        "name": "extract_company_info",
        "description": "Extracts company information from the provided text. Response should be 'unknown' if unable to determine high quality and accurate response from text. Always use british english.",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A description of the company focusing on its product/offering (approx. 200 words).",
                },
                "sector": {
                    "type": "string",
                    "description": "The sector the company operates in.",
                },
                "size": {
                    "type": "string",
                    "description": "Company size category based on employees and revenue.",
                    "enum": ["unknown", "micro", "small", "medium", "large"],
                },
                "business_type": {
                    "type": "string",
                    "description": "The type of business (B2B, B2C, etc.).",
                },
                "technology_stack": {
                    "type": "string",
                    "description": "Main technologies used by the company.",
                },
                "areas_of_focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Markets and business areas the company focuses on.",
                },
            },
            "required": [
                "description",
                "sector",
                "size",
                "business_type",
                "technology_stack",
                "areas_of_focus",
            ],
        },
    }

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert that systematically reads, understands and consolidates company information. Always use british english.",
                },
                {"role": "user", "content": raw_evidence},
            ],
            functions=[company_info_function],
            function_call={"name": "extract_company_info"},
        )

        function_call = response.choices[0].message.function_call
        return json.loads(function_call.arguments)

    except Exception as e:
        print(f"Error analyzing company evidence: {str(e)}")
        return {}


def parse_evidence_file(content: str, company_name: str, file_type: str) -> str:
    """Parse evidence file content for company information."""
    system_prompt = (
        "Within the following content find company information based on the following areas. "
        "If unable to determine high quality and accurate response from text then don't include that area of information in your response. "
        "A description of the company. Approx 200 words which would enable someone with no knowledge of the company to understand the company "
        "and what they do / are known for. It should focus on what the companies product / offering is rather than its technology or implementation "
        "unless that is core to it's offering. "
        "The sector the company operates in. i.e. consumer electronics, financial markets... "
        "The size of the company (unknown, micro, small, medium, large). "
        "The type of business the company is. Is it a b2b, b2c maybe a mix of multiple. "
        "The main technologies used by the company and it's platforms. "
        "Areas of focus of the company. The markets it focuses on i.e education, consumer, finance, entertainment "
        "and the types of business it does i.e. product manufacture, software development... "
        f"The company is called {company_name} and the file your extracting data from is a {file_type}"
    )

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=500,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error parsing evidence file: {str(e)}")
        return ""
