# backend/tools/currency_converter.py

import httpx
from typing import Dict, Any
import os
from ..utils.cache import cache_get, cache_set
from dotenv import load_dotenv
load_dotenv()

# Using free tier API from exchangerate-api.com (no API key needed for basic usage)
# Alternative: exchangerate.host also available
API_URL = "https://api.exchangerate-api.com/v4/latest"
# Optional API key for higher rate limits
API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")

def convert_currency_amount(
    from_currency: str,
    to_currency: str,
    amount: float
) -> Dict[str, Any]:
    """
    Converts currency using exchangerate-api.com free API.
    Includes:
    - Real-time conversion without API key requirement
    - Caching
    - Formatted response with metadata
    - Better error handling
    """

    # Normalize currency codes
    from_currency = from_currency.upper().strip()
    to_currency = to_currency.upper().strip()
    
    # Validate amount
    if amount <= 0:
        return {
            "status": "error",
            "message": "Amount must be greater than 0",
            "from": from_currency,
            "to": to_currency,
            "amount": amount
        }

    cache_key = f"FX:{from_currency}:{to_currency}:{amount}"
    cached = cache_get(cache_key)
    if cached and "rate" in cached:
        # Add cache indicator
        cached["from_cache"] = True
        return cached

    try:
        # Use free API from exchangerate-api.com
        url = f"{API_URL}/{from_currency}"

        print(f"[Currency Converter] Fetching from API: {from_currency} -> {to_currency} ({amount})")
        
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()

        data = resp.json()
        
        # Check if API returned error (look for 'error' key or checking if we have rates)
        if "error" in data:
            error_msg = data.get("error", {}).get("info", "Unknown error")
            print(f"[Currency Converter] API Error: {error_msg}")
            return {
                "status": "error",
                "message": f"API Error: {error_msg}",
                "from": from_currency,
                "to": to_currency,
                "amount": amount
            }

        # Get the exchange rate for target currency
        rates = data.get("rates", {})
        if not rates:
            print(f"[Currency Converter] No rates returned from API")
            return {
                "status": "error",
                "message": "No exchange rates returned from API",
                "from": from_currency,
                "to": to_currency,
                "amount": amount
            }
            
        if to_currency not in rates:
            print(f"[Currency Converter] Invalid target currency: {to_currency}")
            return {
                "status": "error",
                "message": f"Invalid currency code: {to_currency}",
                "from": from_currency,
                "to": to_currency,
                "amount": amount
            }

        # Calculate conversion
        rate = float(rates[to_currency])
        converted = float(amount * rate)

        result = {
            "status": "success",
            "from": from_currency,
            "to": to_currency,
            "amount": amount,
            "rate": round(rate, 4),
            "converted_amount": round(converted, 2),
            "from_cache": False,
            "formatted_result": f"{amount} {from_currency} = {converted:.2f} {to_currency} (Rate: {rate:.4f})"
        }

        cache_set(cache_key, result)
        print(f"[Currency Converter] Success: {result['formatted_result']}")
        return result

    except httpx.RequestError as e:
        print(f"[Currency Converter] Request Error: {str(e)}")
        return {
            "status": "error",
            "message": f"Network error: {str(e)}. Please check your internet connection.",
            "from": from_currency,
            "to": to_currency,
            "amount": amount
        }
    
    except ValueError as e:
        print(f"[Currency Converter] Value Error: {str(e)}")
        return {
            "status": "error",
            "message": f"Invalid currency code or conversion failed: {str(e)}",
            "from": from_currency,
            "to": to_currency,
            "amount": amount
        }
    
    except Exception as e:
        print(f"[Currency Converter] Unexpected Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}",
            "from": from_currency,
            "to": to_currency,
            "amount": amount
        }
