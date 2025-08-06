import re
from typing import List, Dict, Tuple

class TextPatternProcessor:
    """Class to handle different text file patterns and extract account information"""
    
    def __init__(self):
        self.patterns = {
            'netflix_account': self._pattern_netflix_account,
            'email_password': self._pattern_email_password,
            'cookie_format': self._pattern_cookie_format,
            'generic_text': self._pattern_generic_text
        }
    
    def detect_pattern(self, content: str) -> str:
        """Detect the pattern type of the text content"""
        content_lower = content.lower()
        
        # Check for Netflix account pattern (email:password:details)
        if re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^:]+:', content):
            return 'netflix_account'
        
        # Check for email:password pattern
        elif re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^:\n]+', content):
            return 'email_password'
        
        # Check for cookie format (tab-separated values)
        elif '\t' in content and 'netflix.com' in content_lower:
            return 'cookie_format'
        
        # Default to generic text
        else:
            return 'generic_text'
    
    def _parse_accounts_by_email_boundaries(self, content: str) -> List[str]:
        """Parse accounts based on email boundaries - new account starts with email/@ line"""
        lines = content.strip().split('\n')
        accounts = []
        current_account = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Check if this line starts with an email (contains @ symbol)
            if '@' in line and re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', line):
                # If we have a previous account, save it
                if current_account:
                    accounts.append('\n'.join(current_account))
                    current_account = []
                
                # Start new account
                current_account.append(line)
            else:
                # Continue with current account
                if current_account:
                    current_account.append(line)
                else:
                    # If no account started yet, treat as standalone line
                    current_account.append(line)
        
        # Add the last account if exists
        if current_account:
            accounts.append('\n'.join(current_account))
        
        return accounts
    
    def _pattern_netflix_account(self, content: str) -> Dict:
        """Process Netflix account pattern: email:password:details"""
        accounts = []
        account_blocks = self._parse_accounts_by_email_boundaries(content)
        
        for account_block in account_blocks:
            lines = account_block.strip().split('\n')
            if not lines:
                continue
            
            # Process the first line which should contain email:password:details
            first_line = lines[0].strip()
            if not first_line:
                continue
            
            # Split by first two colons to get email, password, and details
            parts = first_line.split(':', 2)
            if len(parts) >= 2:
                email = parts[0].strip()
                password = parts[1].strip()
                details = parts[2].strip() if len(parts) > 2 else ""
                
                # Extract additional information from details
                account_info = {
                    'email': email,
                    'password': password,
                    'country': self._extract_country(details),
                    'member_plan': self._extract_member_plan(details),
                    'member_since': self._extract_member_since(details),
                    'video_quality': self._extract_video_quality(details),
                    'phone_number': self._extract_phone_number(details),
                    'max_streams': self._extract_max_streams(details),
                    'payment_type': self._extract_payment_type(details),
                    'is_verified': self._extract_verified_status(details),
                    'total_cc': self._extract_total_cc(details),
                    'cookies': self._extract_cookies(details),
                    'full_content': account_block  # Store the full account content
                }
                
                accounts.append(account_info)
        
        return {
            'pattern_type': 'netflix_account',
            'total_accounts': len(accounts),
            'accounts': accounts
        }
    
    def _pattern_email_password(self, content: str) -> Dict:
        """Process email:password pattern"""
        accounts = []
        account_blocks = self._parse_accounts_by_email_boundaries(content)
        
        for account_block in account_blocks:
            lines = account_block.strip().split('\n')
            if not lines:
                continue
            
            # Process the first line which should contain email:password
            first_line = lines[0].strip()
            if not first_line:
                continue
            
            # Split by colon to get email and password
            parts = first_line.split(':', 1)
            if len(parts) == 2:
                email = parts[0].strip()
                password = parts[1].strip()
                
                account_info = {
                    'email': email,
                    'password': password,
                    'details': 'No additional details provided',
                    'full_content': account_block  # Store the full account content
                }
                
                accounts.append(account_info)
        
        return {
            'pattern_type': 'email_password',
            'total_accounts': len(accounts),
            'accounts': accounts
        }
    
    def _pattern_cookie_format(self, content: str) -> Dict:
        """Process cookie format (tab-separated values)"""
        return {
            'pattern_type': 'cookie_format',
            'total_accounts': 1,
            'accounts': [{
                'content': content,
                'type': 'cookie_data'
            }]
        }
    
    def _pattern_generic_text(self, content: str) -> Dict:
        """Process generic text content"""
        return {
            'pattern_type': 'generic_text',
            'total_accounts': 1,
            'accounts': [{
                'content': content,
                'type': 'generic_data'
            }]
        }
    
    def _extract_country(self, details: str) -> str:
        """Extract country from details string"""
        match = re.search(r'Country\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_member_plan(self, details: str) -> str:
        """Extract member plan from details string"""
        match = re.search(r'memberPlan\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_member_since(self, details: str) -> str:
        """Extract member since from details string"""
        match = re.search(r'memberSince\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_video_quality(self, details: str) -> str:
        """Extract video quality from details string"""
        match = re.search(r'videoQuality\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_phone_number(self, details: str) -> str:
        """Extract phone number from details string"""
        match = re.search(r'phonenumber\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_max_streams(self, details: str) -> str:
        """Extract max streams from details string"""
        match = re.search(r'maxStreams\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_payment_type(self, details: str) -> str:
        """Extract payment type from details string"""
        match = re.search(r'paymentType\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_verified_status(self, details: str) -> str:
        """Extract verified status from details string"""
        match = re.search(r'isVerified\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_total_cc(self, details: str) -> str:
        """Extract total CC from details string"""
        match = re.search(r'Total_CC\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _extract_cookies(self, details: str) -> str:
        """Extract cookies from details string"""
        match = re.search(r'Cookies\s*=\s*([^|]+)', details)
        return match.group(1).strip() if match else "Unknown"
    
    def _escape_markdown(self, text: str) -> str:
        """Escape special characters for Telegram Markdown"""
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text
    
    def process_text_file(self, content: str) -> Dict:
        """Main method to process text file content"""
        pattern_type = self.detect_pattern(content)
        processor_method = self.patterns.get(pattern_type, self._pattern_generic_text)
        return processor_method(content)
    
    def format_response(self, result: Dict) -> str:
        """Format the processing result into a readable response"""
        pattern_type = result.get('pattern_type', 'unknown')
        total_accounts = result.get('total_accounts', 0)
        accounts = result.get('accounts', [])
        
        response = "📄 Text File Analysis Complete:\n\n"
        response += f"🔍 Pattern: {pattern_type.replace('_', ' ').title()}\n"
        response += f"📊 Total Accounts: {total_accounts}\n\n"
        
        # Always show minimal info and direct to file
        if total_accounts > 0:
            response += "📋 Summary:\n"
            if pattern_type == 'netflix_account':
                response += f"✅ Found {total_accounts} Netflix accounts\n"
                response += "📄 Complete details in attached file\n"
                if accounts:
                    account = accounts[0]
                    response += f"📧 Email: {self._escape_markdown(account.get('email', 'N/A'))}\n"
                    response += f"🌍 Country: {self._escape_markdown(account.get('country', 'N/A'))}\n"
                    response += f"📺 Plan: {self._escape_markdown(account.get('member_plan', 'N/A'))}\n"
            
            elif pattern_type == 'email_password':
                response += f"✅ Found {total_accounts} email:password pairs\n"
                response += "📄 Complete details in attached file\n"
                if accounts:
                    account = accounts[0]
                    response += f"📧 Email: {self._escape_markdown(account.get('email', 'N/A'))}\n"
        
        if pattern_type == 'cookie_format':
            response += "🍪 Cookie Data Detected\n"
            response += "Use ZIP processing for better results"
        
        elif pattern_type == 'generic_text':
            response += "📝 Generic Text Content\n"
            response += "Use ZIP processing for better results"
        
        return response

    def format_text_file_content(self, result: Dict) -> str:
        """Format the processing result into a text file content with ---- separators"""
        pattern_type = result.get('pattern_type', 'unknown')
        total_accounts = result.get('total_accounts', 0)
        accounts = result.get('accounts', [])
        
        content = f"Text File Analysis Results\n"
        content += f"Pattern Detected: {pattern_type.replace('_', ' ').title()}\n"
        content += f"Total Accounts Found: {total_accounts}\n"
        content += "=" * 50 + "\n\n"
        
        if pattern_type == 'netflix_account':
            for i, account in enumerate(accounts, 1):
                content += f"Account {i}:\n"
                content += f"Email: {account.get('email', 'N/A')}\n"
                content += f"Password: {account.get('password', 'N/A')}\n"
                content += f"Country: {account.get('country', 'N/A')}\n"
                content += f"Member Plan: {account.get('member_plan', 'N/A')}\n"
                content += f"Member Since: {account.get('member_since', 'N/A')}\n"
                content += f"Video Quality: {account.get('video_quality', 'N/A')}\n"
                content += f"Phone Number: {account.get('phone_number', 'N/A')}\n"
                content += f"Max Streams: {account.get('max_streams', 'N/A')}\n"
                content += f"Payment Type: {account.get('payment_type', 'N/A')}\n"
                content += f"Is Verified: {account.get('is_verified', 'N/A')}\n"
                content += f"Total CC: {account.get('total_cc', 'N/A')}\n"
                content += f"Cookies: {account.get('cookies', 'N/A')}\n"
                
                if i < len(accounts):
                    content += "\n" + "=" * 50 + "\n\n"
        
        elif pattern_type == 'email_password':
            for i, account in enumerate(accounts, 1):
                content += f"Account {i}:\n"
                content += f"Email: {account.get('email', 'N/A')}\n"
                content += f"Password: {account.get('password', 'N/A')}\n"
                content += f"Details: {account.get('details', 'N/A')}\n"
                
                if i < len(accounts):
                    content += "\n" + "=" * 50 + "\n\n"
        
        elif pattern_type == 'cookie_format':
            content += "Cookie Data Detected:\n"
            content += "This appears to be Netflix cookie data in tab-separated format.\n"
            content += "You can use this with the ZIP processing feature for better results.\n"
            content += "\n" + "=" * 50 + "\n\n"
        
        else:
            content += "Generic Text Content:\n"
            content += "This appears to be generic text content.\n"
            content += "For better processing, consider using the ZIP file feature.\n"
            content += "\n" + "=" * 50 + "\n\n"
        
        return content

# Global instance for easy access
text_processor = TextPatternProcessor() 