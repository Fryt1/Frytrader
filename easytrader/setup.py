from setuptools import setup

setup(
    name="easytrader",
    version="0.24.0",
    description="A utility for China Stock Trade",
    long_description=open("../README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="shidenggui",
    author_email="longlyshidenggui@gmail.com",
    license="BSD",
    url="https://github.com/shidenggui/easytrader",
    keywords="China stock trade",
    python_requires=">=3.6",
    install_requires=[
        "requests>=2.25.0",
        "six>=1.15.0",
        "easyutils>=0.1.7",
        "flask>=1.1.0",
        "pywinauto>=0.6.8; sys_platform == 'win32'",
        "pillow>=8.0.0",
        "pandas>=1.1.0", 
        "pytesseract>=0.3.7",
        "opencv-python>=4.5.0",
        "beautifulsoup4>=4.9.0",
        "bs4>=0.0.1",
        "certifi>=2020.6.20",
        "chardet>=3.0.4",
        "click>=7.0",
        "cssselect>=1.1.0",
        "dill>=0.3.0",
        "idna>=2.10",
        "itsdangerous>=1.1.0",
        "jinja2>=2.11.0",
        "lxml>=4.6.0",
        "markupsafe>=1.1.0",
        "numpy>=1.19.0",
        "pyperclip>=1.8.0",
        "pyquery>=1.4.0",
        "python-dateutil>=2.8.0",
        "pytz>=2020.1",
        "urllib3>=1.26.0",
        "werkzeug>=1.0.0"
    ],
    extras_require={
        "all": ["xtquant"],
        "miniqmt": ["xtquant"]
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3.6",
        "License :: OSI Approved :: BSD License",
    ],
    packages=[
        "easytrader",
        "easytrader.config",
        "easytrader.utils",
        "easytrader.miniqmt"
    ],
    package_data={
        "": ["*.jar", "*.json"],
        "config": ["config/*.json"],
    },
)
