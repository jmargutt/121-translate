from uuid import uuid4
import json
import pandas as pd
import os
from time import sleep
import glob
import re
import requests
import click

translations = {}  # store translations to save time and money if they are repeated
immutable_words = [
    {'text': 'PA',
     'regex': False},
    {'text': 'OK',
     'regex': False},
    {'text': r'\{\{.*?\}\}',
     'regex': True},
]
regex_words = {}


def df_to_formatted_json(df, sep="."):
    """
    The opposite of json_normalize
    """
    result = []
    for idx, row in df.iterrows():
        parsed_row = {}
        for col_label, v in row.items():
            keys = col_label.split(sep)

            current = parsed_row
            for i, k in enumerate(keys):
                if i == len(keys)-1:
                    current[k] = v
                else:
                    if k not in current.keys():
                        current[k] = {}
                    current = current[k]
        # save
        result.append(parsed_row)
    return result[0]


def flatten_json(o: dict):
    df = pd.json_normalize(o, sep='.')
    return df.to_dict(orient='records')[0]


def nest_json(o: dict):
    df = pd.DataFrame.from_records(o, index=[0])
    return df_to_formatted_json(df)


def translate_field(value: str, source: str, target: str, trans_headers: dict):
    """Translate field using Azure AI Translator"""
    trans = value
    translation_done = False
    retry_times = 0
    if value in translations.keys():
        trans = translations[value]
        translation_done = True
    else:
        while (not translation_done) and (retry_times <= 10):
            try:
                value_original = value
                
                for ix, word in enumerate(immutable_words):
                    if not word['regex']:
                        value = value.replace(word['text'], f'{ix}{ix}{ix}')
                    else:
                        regex_words[word['text']] = {}
                        occurrences = re.findall(word['text'], value)
                        for ix2, occurrence in enumerate(occurrences):
                            value = value.replace(occurrence, f'{ix}{ix}RR{ix2}{ix2}')
                            regex_words[word['text']][occurrence] = f'{ix}{ix}RR{ix2}{ix2}'
                
                request = requests.post(
                    'https://api.cognitive.microsofttranslator.com/translate',
                    params={'api-version': '3.0', 'from': source, 'to': target},
                    headers=trans_headers,
                    json=[{'text': value}]
                )
                response = request.json()
                trans = response[0]['translations'][0]['text']
                
                for ix, word in enumerate(immutable_words):
                    if not word['regex']:
                        trans = trans.replace(f'{ix}{ix}{ix}', word['text'])
                    else:
                        for key, value in regex_words[word['text']].items():
                            trans = trans.replace(value, key)
                
                translations[value_original] = trans
                translation_done = True
            
            except Exception as e:
                retry_times += 1
                sleep(10)
    if not translation_done:
        print(f"unable to translate {value_original}: {e}")
    return trans


@click.command()
@click.option('--key', default=None, help="Azure AI Translator API Key")
@click.option('--assets', default=None, help="path to 121 assets to translate (.json)")
@click.option('--verbose', '-v', is_flag=True, default=False, help='print more output')
def translate_diff(key, assets, verbose):
    """Translate 121 Portal."""
    
    if key is None or assets is None:
        from dotenv import load_dotenv
        load_dotenv()
        key = str(os.getenv("MSCOGNITIVE_KEY"))
        assets = str(os.getenv("121_ASSETS_PATH"))
    
    # initialize Azure AI Translator headers
    trans_headers = {
        'Ocp-Apim-Subscription-Key': key,
        'Ocp-Apim-Subscription-Region': "westeurope",
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid4())
    }
    
    # get list of available languages
    # repo = Github().get_repo("global-121/121-platform")
    # languages = [Path(x.path).stem for x in repo.get_contents("interfaces/Portal/src/assets/i18n")]
    languages = [os.path.basename(lang_file.replace('.json', '')) for lang_file in glob.glob(f"{assets}/*.json")]
    languages.remove('en')
    if verbose:
        print('found these languages:', languages)
    
    # compare the current english version with the last release
    with open(f"{assets}/en.json", 'r') as jsonFile:
        en_new = flatten_json(json.load(jsonFile))
    releases = requests.get("https://api.github.com/repos/global-121/121-platform/releases").json()
    releases_tags = [x['tag_name'] for x in releases]
    en_old = flatten_json(requests.get(
        f'https://raw.githubusercontent.com/global-121/121-platform/'
        f'{releases_tags[0]}/interfaces/Portal/src/assets/i18n/en.json').json())
    
    for language in languages:
        if verbose:
            print(f"checking translation en --> {language}")
        
        translations.clear()
        with open(f"{assets}/{language}.json", 'r') as jsonFile:
            ln_new = flatten_json(json.load(jsonFile))
        try:
            ln_old = flatten_json(requests.get(
                f'https://raw.githubusercontent.com/global-121/121-platform/'
                f'{releases_tags[0]}/interfaces/Portal/src/assets/i18n/{language}.json').json())
        except requests.exceptions.JSONDecodeError:
            ln_old = {}
        
        for key, value in en_new.items():
            # if key is new, translate
            if key not in en_old.keys() or key not in ln_new.keys() or key not in ln_old.keys():
                ln_new[key] = translate_field(value, "en", language, trans_headers)
            # if value has changed in english, but not in language, translate
            elif value != en_old[key] and ln_new[key] == ln_old[key]:
                ln_new[key] = translate_field(value, "en", language, trans_headers)
        ln_new = nest_json(ln_new)
        
        if ln_new != ln_old:
            if verbose:
                print(f'{language}.json has been updated')
            # save new translations to json
            with open(f'{assets}/{language}.json', 'w', encoding='utf-8') as jsonFile:
                json.dump(
                    ln_new,
                    jsonFile,
                    sort_keys=True,
                    indent=4,
                    separators=(',', ': '),
                    ensure_ascii=False
                )
                jsonFile.write("\n")
            
            
if __name__ == "__main__":
    translate_diff()
