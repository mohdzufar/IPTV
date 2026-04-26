import os
import glob

def flatten_m3u8(channels_dir="Channels", output_file="Main.m3u8"):
    header = '#EXTM3U\n'
    
    # Get all .m3u8 files in Channels/ and subdirectories
    m3u8_files = glob.glob(os.path.join(channels_dir, '**', '*.m3u8'), recursive=True)
    
    with open(output_file, 'w', encoding='utf-8') as outfile:
        outfile.write(header)
        
        for filepath in m3u8_files:
            # Skip the output file itself if it's inside channels_dir (safety)
            if os.path.abspath(filepath) == os.path.abspath(output_file):
                continue
            
            try:
                with open(filepath, 'r', encoding='utf-8') as infile:
                    content = infile.read()
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
                continue
            
            lines = content.splitlines()
            
            # Remove leading #EXTM3U if present
            if lines and lines[0].startswith('#EXTM3U'):
                lines = lines[1:]
            
            # Determine group tag from immediate parent folder (if inside a subfolder)
            group = None
            rel_path = os.path.relpath(filepath, channels_dir)
            parts = rel_path.split(os.sep)
            if len(parts) > 1:   # file is inside a subfolder
                group = parts[0]  # the subfolder name
            
            # Insert group tag before the first #EXTINF line
            if group and lines:
                # Find index of first #EXTINF
                insert_idx = next((i for i, line in enumerate(lines) if line.startswith('#EXTINF')), None)
                if insert_idx is not None:
                    lines.insert(insert_idx, f'#EXTGRP:{group}')
                else:
                    # No #EXTINF lines – still add group at the start (if the file is non-empty)
                    lines.insert(0, f'#EXTGRP:{group}')
            
            # Write the (possibly modified) content
            outfile.write('\n'.join(lines) + '\n')
    
    print(f"Flattened {len(m3u8_files)} files into {output_file}")

if __name__ == "__main__":
    flatten_m3u8()
