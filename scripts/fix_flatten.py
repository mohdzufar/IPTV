import re

with open('Channels/Flatten.m3u8', 'r', encoding='utf-8') as f:
    content = f.read()

# Insert a newline wherever ".m3u8" is immediately followed by "#EXTINF" (with any whitespace)
content = re.sub(r'(\.m3u8)\s+(#EXTINF)', r'\1\n\2', content)

with open('Channels/Flatten.m3u8', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed missing line breaks in Flatten.m3u8")
