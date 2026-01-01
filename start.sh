#!/bin/bash

# Third Wheel Backend - Local Development Startup Script

echo "🚀 Starting Third Wheel Backend..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo "📝 Please copy .env.example to .env and configure your credentials:"
    echo "   cp .env.example .env"
    exit 1
fi

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed"
    echo "📝 Please install Python 3.11 or higher"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

# Check if PostgreSQL is accessible
echo "🔍 Checking database connection..."
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()

db_url = os.getenv('DATABASE_URL')
if not db_url:
    print('❌ Error: DATABASE_URL not set in .env')
    exit(1)
print('✅ DATABASE_URL configured')
" || exit 1

# Start the server
echo "✅ Starting FastAPI server on http://localhost:8000"
echo "📚 API docs will be available at http://localhost:8000/docs"
echo "🔌 WebSocket endpoint: ws://localhost:8000/ws/chat/{session_id}?token=JWT"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
