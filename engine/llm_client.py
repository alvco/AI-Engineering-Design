"""
LLM Client for Level 3 Synthesis Engine

Handles API calls to Claude (Anthropic) and Gemini (Google) with role-specific
temperature settings, system/user prompt separation, and detailed token tracking
for budget management.
"""

import logging
import time
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logging.warning("Anthropic SDK not available. Claude calls will fail.")

try:
    import google.generativeai as genai
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logging.warning("Google GenerativeAI SDK not available. Gemini calls will fail.")


@dataclass
class LLMResponse:
    """Structured response from LLM API call with detailed token tracking"""
    content: str
    model: str
    temperature: float
    success: bool
    error: Optional[str] = None
    input_tokens: Optional[int] = None     # Input tokens used (for budget tracking)
    output_tokens: Optional[int] = None    # Output tokens generated (for budget tracking)
    total_tokens: Optional[int] = None     # Total tokens (input + output)
    response_time: Optional[float] = None
    
    def __post_init__(self):
        """Calculate total_tokens if not provided"""
        if self.total_tokens is None and self.input_tokens is not None and self.output_tokens is not None:
            self.total_tokens = self.input_tokens + self.output_tokens


class LLMClient:
    """
    Client for calling LLM APIs with role-specific configurations and budget tracking.
    
    Supports:
    - Claude (Anthropic API)
    - Gemini (Google GenerativeAI API) 
    - System/user prompt separation
    - Role-specific temperature settings
    - Detailed token usage tracking for budget management
    - Retry logic for network issues and rate limits
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize LLM client with configuration.
        
        Args:
            config: Configuration dict from config.yaml
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Initialize API clients
        self._init_anthropic()
        self._init_google()
        
        # Role-specific settings
        self.synthesis_temperature = config['api_settings']['synthesis_temperature']
        self.validator_temperature = config['api_settings']['validator_temperature']
        self.max_tokens = config['api_settings']['max_tokens']
        self.timeout = config['api_settings']['timeout']
        
        # Default models
        self.default_synthesis_model = config['models']['synthesis_agent']
        self.default_validator_model = config['models']['physics_validator']
        
        # Budget tracking
        self.budget_config = config.get('budget_management', {})
        self.budget_enabled = self.budget_config.get('enabled', False)
        self.per_call_limit = self.budget_config.get('per_call_limit', 4000)
        self.track_usage = self.budget_config.get('tracking', {}).get('log_per_call', False)
        
        # Running totals for logging
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_api_calls = 0
        
        self.logger.info(f"LLMClient initialized with synthesis_temp={self.synthesis_temperature}, "
                        f"validator_temp={self.validator_temperature}, budget_tracking={self.budget_enabled}")

    def _init_anthropic(self):
        """Initialize Anthropic client"""
        if ANTHROPIC_AVAILABLE:
            try:
                self.anthropic_client = anthropic.Anthropic()
                self.logger.info("Anthropic client initialized successfully")
            except Exception as e:
                self.logger.error(f"Failed to initialize Anthropic client: {e}")
                self.anthropic_client = None
        else:
            self.anthropic_client = None

    def _init_google(self):
        """Initialize Google GenerativeAI client"""
        if GOOGLE_AVAILABLE:
            try:
                # Note: API key should be set via environment variable GOOGLE_API_KEY
                self.google_client = genai
                self.logger.info("Google GenerativeAI client initialized successfully")
            except Exception as e:
                self.logger.error(f"Failed to initialize Google client: {e}")
                self.google_client = None
        else:
            self.google_client = None

    def _check_token_limit(self, estimated_tokens: int) -> bool:
        """
        Check if estimated tokens would exceed per-call limit.
        
        Args:
            estimated_tokens: Estimated tokens for the call
            
        Returns:
            True if within limit, False if would exceed
        """
        if not self.budget_enabled:
            return True
            
        if estimated_tokens > self.per_call_limit:
            self.logger.warning(f"Estimated tokens ({estimated_tokens}) exceeds per-call limit ({self.per_call_limit})")
            return False
            
        return True

    def _log_token_usage(self, response: LLMResponse):
        """
        Log token usage for budget tracking.
        
        Args:
            response: LLM response with token information
        """
        if self.track_usage and response.success:
            self.total_input_tokens += response.input_tokens or 0
            self.total_output_tokens += response.output_tokens or 0
            self.total_api_calls += 1
            
            self.logger.info(f"Token usage: +{response.input_tokens}in/+{response.output_tokens}out, "
                           f"Total: {self.total_input_tokens + self.total_output_tokens} "
                           f"({self.total_api_calls} calls)")

    def call_synthesis_agent(self, 
                           system_prompt: str, 
                           user_prompt: str,
                           model: Optional[str] = None,
                           temperature: Optional[float] = None) -> LLMResponse:
        """
        Call synthesis agent with appropriate temperature for creative design.
        
        Args:
            system_prompt: Role definition and static instructions
            user_prompt: Current task and dynamic content
            model: Override default model
            temperature: Override default temperature
            
        Returns:
            LLMResponse with content and detailed token metadata
        """
        model = model or self.default_synthesis_model
        temperature = temperature if temperature is not None else self.synthesis_temperature
        
        # Rough estimate: ~1 token per 4 characters
        estimated_tokens = (len(system_prompt) + len(user_prompt)) // 4 + self.max_tokens
        
        if not self._check_token_limit(estimated_tokens):
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error=f"Estimated tokens ({estimated_tokens}) exceeds per-call limit ({self.per_call_limit})"
            )
        
        self.logger.info(f"Calling synthesis agent: model={model}, temp={temperature}")
        
        response = self._call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=temperature,
            role="synthesis"
        )
        
        self._log_token_usage(response)
        return response

    def call_validator(self, 
                      system_prompt: str,
                      user_prompt: str,
                      model: Optional[str] = None,
                      temperature: Optional[float] = None) -> LLMResponse:
        """
        Call validator with low temperature for deterministic grading.
        
        Args:
            system_prompt: Role definition and static instructions
            user_prompt: Current validation task
            model: Override default model
            temperature: Override default temperature
            
        Returns:
            LLMResponse with content and detailed token metadata
        """
        model = model or self.default_validator_model
        temperature = temperature if temperature is not None else self.validator_temperature
        
        # Rough estimate: ~1 token per 4 characters
        estimated_tokens = (len(system_prompt) + len(user_prompt)) // 4 + self.max_tokens
        
        if not self._check_token_limit(estimated_tokens):
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error=f"Estimated tokens ({estimated_tokens}) exceeds per-call limit ({self.per_call_limit})"
            )
        
        self.logger.info(f"Calling validator: model={model}, temp={temperature}")
        
        response = self._call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=temperature,
            role="validator"
        )
        
        self._log_token_usage(response)
        return response

    def _call_llm(self, 
                  system_prompt: str,
                  user_prompt: str, 
                  model: str,
                  temperature: float,
                  role: str) -> LLMResponse:
        """
        Internal method to route LLM calls based on model type.
        
        Args:
            system_prompt: System message content
            user_prompt: User message content
            model: Model identifier
            temperature: Sampling temperature
            role: Role type for logging
            
        Returns:
            LLMResponse with content and detailed token metadata
        """
        start_time = time.time()
        
        try:
            if model.startswith('claude'):
                return self._call_anthropic(system_prompt, user_prompt, model, temperature, role)
            elif model.startswith('gemini'):
                return self._call_google(system_prompt, user_prompt, model, temperature, role)
            else:
                return LLMResponse(
                    content="",
                    model=model,
                    temperature=temperature,
                    success=False,
                    error=f"Unsupported model: {model}",
                    response_time=time.time() - start_time
                )
                
        except Exception as e:
            self.logger.error(f"LLM call failed for {model}: {e}")
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error=str(e),
                response_time=time.time() - start_time
            )

    def _call_anthropic(self, 
                       system_prompt: str,
                       user_prompt: str,
                       model: str,
                       temperature: float,
                       role: str) -> LLMResponse:
        """
        Call Anthropic Claude API with system/user message separation.
        
        Args:
            system_prompt: System message content
            user_prompt: User message content  
            model: Claude model identifier
            temperature: Sampling temperature
            role: Role type for logging
            
        Returns:
            LLMResponse with Claude's response and detailed token tracking
        """
        if not self.anthropic_client:
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error="Anthropic client not available"
            )

        start_time = time.time()
        
        try:
            # Retry logic for network issues
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = self.anthropic_client.messages.create(
                        model=model,
                        max_tokens=self.max_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[
                            {"role": "user", "content": user_prompt}
                        ],
                        timeout=self.timeout
                    )
                    
                    content = response.content[0].text if response.content else ""
                    
                    # Extract detailed token usage from Claude response
                    input_tokens = getattr(response.usage, 'input_tokens', None) if hasattr(response, 'usage') else None
                    output_tokens = getattr(response.usage, 'output_tokens', None) if hasattr(response, 'usage') else None
                    total_tokens = getattr(response.usage, 'total_tokens', None) if hasattr(response, 'usage') else None
                    
                    # Calculate total if not provided
                    if total_tokens is None and input_tokens is not None and output_tokens is not None:
                        total_tokens = input_tokens + output_tokens
                    
                    self.logger.info(f"Anthropic call successful: {len(content)} chars, "
                                   f"{input_tokens}in/{output_tokens}out/{total_tokens}total tokens")
                    
                    return LLMResponse(
                        content=content,
                        model=model,
                        temperature=temperature,
                        success=True,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        response_time=time.time() - start_time
                    )
                    
                except anthropic.APITimeoutError as e:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff
                        self.logger.warning(f"Anthropic timeout, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise e
                        
                except anthropic.RateLimitError as e:
                    if attempt < max_retries - 1:
                        wait_time = 10 + (2 ** attempt)  # Longer wait for rate limits
                        self.logger.warning(f"Anthropic rate limit, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise e
                        
        except Exception as e:
            self.logger.error(f"Anthropic API call failed: {e}")
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error=str(e),
                response_time=time.time() - start_time
            )

    def _call_google(self, 
                    system_prompt: str,
                    user_prompt: str,
                    model: str,
                    temperature: float,
                    role: str) -> LLMResponse:
        """
        Call Google Gemini API with system/user prompt combination.
        
        Note: Gemini doesn't have separate system messages, so we combine them.
        
        Args:
            system_prompt: System message content
            user_prompt: User message content
            model: Gemini model identifier
            temperature: Sampling temperature
            role: Role type for logging
            
        Returns:
            LLMResponse with Gemini's response and detailed token tracking
        """
        if not self.google_client:
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error="Google GenerativeAI client not available"
            )

        start_time = time.time()
        
        try:
            # Combine system and user prompts for Gemini
            combined_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
            
            # Configure the model
            generation_config = {
                'temperature': temperature,
                'max_output_tokens': self.max_tokens,
            }
            
            model_instance = self.google_client.GenerativeModel(
                model_name=model,
                generation_config=generation_config
            )
            
            # Retry logic for network issues
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = model_instance.generate_content(combined_prompt)
                    
                    content = response.text if response.text else ""
                    
                    # Extract detailed token usage from Gemini response
                    # Fix: usage_metadata is Proto object, access fields directly
                    input_tokens = None
                    output_tokens = None
                    total_tokens = None
                    
                    if hasattr(response, 'usage_metadata'):
                        usage = response.usage_metadata
                        input_tokens = getattr(usage, 'prompt_token_count', None)
                        output_tokens = getattr(usage, 'candidates_token_count', None)
                        total_tokens = getattr(usage, 'total_token_count', None)
                        
                        # Calculate total if not provided
                        if total_tokens is None and input_tokens is not None and output_tokens is not None:
                            total_tokens = input_tokens + output_tokens
                    
                    self.logger.info(f"Google call successful: {len(content)} chars, "
                                   f"{input_tokens}in/{output_tokens}out/{total_tokens}total tokens")
                    
                    return LLMResponse(
                        content=content,
                        model=model,
                        temperature=temperature,
                        success=True,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        response_time=time.time() - start_time
                    )
                    
                except Exception as e:
                    if "quota" in str(e).lower() or "rate" in str(e).lower():
                        if attempt < max_retries - 1:
                            wait_time = 10 + (2 ** attempt)
                            self.logger.warning(f"Google rate limit, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise e
                    else:
                        raise e
                        
        except Exception as e:
            self.logger.error(f"Google API call failed: {e}")
            return LLMResponse(
                content="",
                model=model,
                temperature=temperature,
                success=False,
                error=str(e),
                response_time=time.time() - start_time
            )

    def get_token_usage_summary(self) -> Dict[str, Any]:
        """
        Get summary of total token usage across all calls.
        
        Returns:
            Dict with usage statistics
        """
        return {
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_tokens': self.total_input_tokens + self.total_output_tokens,
            'total_api_calls': self.total_api_calls,
            'average_input_per_call': self.total_input_tokens / max(self.total_api_calls, 1),
            'average_output_per_call': self.total_output_tokens / max(self.total_api_calls, 1)
        }

    def reset_usage_tracking(self):
        """Reset token usage counters for new run"""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_api_calls = 0
        self.logger.info("Token usage tracking reset")

    def get_available_models(self) -> Dict[str, bool]:
        """
        Check which model types are available.
        
        Returns:
            Dict mapping model types to availability
        """
        return {
            'claude': self.anthropic_client is not None,
            'gemini': self.google_client is not None
        }

    def validate_model(self, model: str) -> bool:
        """
        Check if a model is supported and available.
        
        Args:
            model: Model identifier to validate
            
        Returns:
            True if model is available, False otherwise
        """
        if model.startswith('claude'):
            return self.anthropic_client is not None
        elif model.startswith('gemini'):
            return self.google_client is not None
        else:
            return False


# Test functions for development
def test_llm_client():
    """Test LLM client with mock configuration"""
    
    # Mock config with budget management
    config = {
        'api_settings': {
            'synthesis_temperature': 0.4,
            'validator_temperature': 0.1,
            'max_tokens': 4000,
            'timeout': 120
        },
        'models': {
            'synthesis_agent': 'claude-sonnet-4-20250514',
            'physics_validator': 'claude-sonnet-4-20250514'
        },
        'budget_management': {
            'enabled': True,
            'per_call_limit': 8000,
            'tracking': {
                'log_per_call': True
            }
        }
    }
    
    client = LLMClient(config)
    
    # Test availability
    available = client.get_available_models()
    print(f"Available models: {available}")
    
    # Test model validation
    print(f"Claude valid: {client.validate_model('claude-sonnet-4-20250514')}")
    print(f"Gemini valid: {client.validate_model('gemini-1.5-pro-002')}")
    print(f"Invalid valid: {client.validate_model('gpt-4')}")
    
    # Test token tracking
    print(f"Initial usage: {client.get_token_usage_summary()}")
    
    # Test synthesis call (will fail without API keys, but tests structure)
    system_prompt = "You are a synthesis agent."
    user_prompt = "Generate a test response."
    
    response = client.call_synthesis_agent(system_prompt, user_prompt)
    print(f"Synthesis response success: {response.success}")
    print(f"Token tracking: input={response.input_tokens}, output={response.output_tokens}")
    if response.error:
        print(f"Expected error: {response.error}")


if __name__ == "__main__":
    test_llm_client()