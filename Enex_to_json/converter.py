import argparse
import shutil
from bs4 import BeautifulSoup
import json
import random
import xml.etree.ElementTree as ET
from scipy.spatial import cKDTree
import hashlib
import os
import base64
import re
from datetime import datetime
import time
from typing import List, Type
import logging
import inspect
from urllib.parse import urlparse
import urllib.request



from models.language_patterns import language_patterns
import models.mime, models.json_model as Model
from models.options import Options
import warnings

# Ignore les avertissements de BeautifulSoup
warnings.filterwarnings("ignore", category=UserWarning, module="bs4")

# Déclarer options en tant que variable globale
my_options = Options()
 # Définition des balises block 
liste_balisesBlock = ['div', 'p', 'hr',  'h1', 'h2', 'h3','h4', 'h5', 'h6','en-media','table', 'img','li', 'pre']


# Configurer le logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)-8s - %(funcName)-2s l.%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler("debug.log")
    ]
)
logger = logging.getLogger(__name__)

class FileInfo:
    def __init__(self, file_id: str, unique_filename: str, original_filename: str, mime_type: str, file_size: int, file_type: str, hash_md5: str):
        """Gestion des infos d'un fichier

        Args:
            file_id (str): random id pour le fichier
            filename (str): Nom du fichier (attention, fichier généré avec hash + filename)
            mime_type (str): mime
            file_size (int): taille
            file_type (str): type Anytype
            hash_md5 (str): hash_md5 si pas présent ailleurs
        """
        self.file_id = file_id
        self.original_filename = original_filename
        self.unique_filename = unique_filename
        self.mime_type = mime_type
        self.file_size = file_size
        self.file_type = file_type
        self.hash_md5 = hash_md5

def log_debug(message: str, level: int = logging.DEBUG):
    """Fonction de gestion des logs.
    Si l'option de debug est activé : 
    - on met tout les log DEBUG ou supérieur dans un fichier de log 
    - et si on exécute sous VS Code, on affiche dans la console les logging.NOSET
    Sans l'option, on affiche les logs supérieurs à DEBUG dans la console 

    Args:
        message (str): message à logger
        level (int, optional): logging level. Defaults to logging.DEBUG.
    """
    caller_frame = inspect.stack()[1]
    caller_func = caller_frame[3].ljust(30)
    caller_lineno = str(caller_frame[2]).ljust(4)
    if my_options.is_debug:
        if level >= logging.DEBUG:
            logger.log(level, f"{caller_func} l.{caller_lineno} - {message}")
        if 'TERM_PROGRAM' in os.environ.keys() and os.environ['TERM_PROGRAM'] == 'vscode': # debug en mode dev, on affiche tout
            print(f"{caller_func} l.{caller_lineno} - {message}")
    if level > logging.DEBUG:
        print(message)
    
        
def sanitize_filename(filename):
    invalid_chars = '/\\?%*:|"<>'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    filename = filename.strip()  # Supprimer les espaces de début et de fin
    filename = filename[:255]  # Limiter la longueur du nom de fichier à 255 caractères
    if not filename:  # Vérifier si le nom de fichier résultant est vide
        filename = "empty_filename"+generate_random_id(6)  # Si vide, attribuer un nom de fichier par défaut
    return filename


def generate_random_id(length = 48):
    """Génère un identifiant aléatoire en hexadécimal de la longueur spécifiée""" 
    
    hex_chars = '0123456789abcdef'
    id = ''.join(random.choice(hex_chars) for _ in range(length))
    return id


def extract_shifting_left(div):
    """Extrait la valeur de margin_left ou padding-left"""
    style = div.get('style')
    if style:
        style_properties = style.split(';')
        for prop in style_properties:
            if 'margin-left' in prop or 'padding-left' in prop:
                value_str = prop.split(':')[1].strip()
                try:
                    # Enlever les unités 'em' ou 'px' de la chaîne pour obtenir la valeur entière
                    if 'em' in value_str:
                        value_int = int(value_str.replace('em', '').strip()) * 16
                    elif 'px' in value_str:
                        value_int = int(value_str.replace('px', '').strip())
                    else:
                        # Si aucune unité n'est spécifiée, retourner une valeur par défaut
                        log_debug(f"Unknown shifting left value: {value_str}", logging.WARNING)
                        return 16
                    return value_int
                except ValueError:
                    # En cas d'erreur de conversion, retourner une valeur par défaut
                    log_debug(f"Invalid shifting left value: {value_str}", logging.WARNING)
                    return 16
    return 0


def extract_tag_info(contenu_div, tags_list):
    """Extract info about tag in this content

    Args:
        contenu_div (_type_): _description_
        tags_list (list): list of tag to treat

    Returns:
        list: {
            'tag_object': tag,
            'text': text into this tag,
            'start': starting position in contenu_div,
            'end': end position in contenu_div
        }
    """    
    # Analyser le contenu HTML   
    log_debug(f"Recherche des chaines mises en forme pour {contenu_div}", logging.NOTSET)
    # On recréé un objet soup à partir de l'objet tag transmis
    contenu_str = str(contenu_div)
    soup = BeautifulSoup(str(contenu_div), 'html.parser')
    
    # Initialiser une liste pour stocker les informations des balises
    tags_info = []

    for tag in soup.find_all(tags_list):     
        text = tag.get_text()

        # On compte le nombre de caractères du texte de contenu_div jusqu'à la position de la balise
        content_to_count = contenu_str[0:tag.sourcepos]
        soup_to_count = BeautifulSoup(content_to_count, 'html.parser')
        start = len(soup_to_count.get_text())
        end = start + len(text)

        log_debug(f"--- 'tag_name': {tag.name}, 'text': {text}, 'start': {start}, 'end': {end}, 'tag position' : {tag.sourcepos}, 'tag_object' : {tag}", logging.NOTSET)

        #Ajouter les informations de la balise à la liste
        tags_info.append({
            'tag_object': tag,
            'text': text,
            'start': start,
            'end': end
        })
        
    return tags_info


def extract_styles(style_string):
    """Génère un dictionnaire des styles à partir de l'attribut style

    Args:
        style_string (string): contenu de l'attribut string

    Returns:
        dic: contient les couples style CSS->valeur
    """
    style_dict = {}
    if style_string:
        # Cas particuliers à gérer :
        ## href:https://example.com
        ## et les background:(...) url(&quot;data:image/svg+xml;base64,PHN2ZyB3(...)
        style_pairs = re.findall(r'([^:]+):([^;]+);*', style_string)
        for key, value in style_pairs:
                style_dict[key.strip()] = value.strip()
    print(style_dict)   
    return style_dict


# couleur AT suivant le RGB Evernote
def extract_color_from_style(style):
    """Transform RGB or Hexa color from Evernote to a AT color (limited)

    Args:
        style (string): "rgb()" or "#xxxxxx"

    Returns:
        string: color name
    """
    
    colors = {
        "grey": (182, 182, 182),
        "yellow": (236, 217, 27),
        "orange": (255, 181, 34),
        "red": (245, 85, 34),
        "pink": (229, 28, 160),
        "purple": (171, 80, 204),
        "blue": (62, 88, 235),
        "ice": (42, 167, 238),
        "teal": (15, 200, 186),
        "lime": (93, 212, 0),
    }
    # For background-color, map 1 to 1
    EN_bck_color ={
        "yellow": (255, 239, 158),
        "orange": (255, 209, 176),
        "red": (254, 193, 208),
        "purple": (203, 202, 255),
        "blue": (176, 236, 244),
        "lime": (183, 247, 209),
        "black": (51,51,51) # couleur mise automatiquement dans certains cas sous EN (pas de black sur AT mais ça sera ignoré)
    }
    default_color="default"

    def rgb_to_tuple(rgb):
        try:
            if "%" in rgb:
                # Gérer les valeurs en pourcentage
                values = [int(float(val[:-1]) * 255 / 100) for val in rgb.split(",")]
            else:
                # Gérer les valeurs entières ou en virgule flottante
                values = [int(float(val)) if "." in val else int(val) for val in rgb.split(",")]

            # Vérifier si le nombre de valeurs est exactement 3
            if len(values) == 3:
                return tuple(values)
            else:
                raise ValueError("Invalid RGB format")

        except (ValueError, IndexError):
            # En cas d'erreur, retourner le tuple par défaut
            return (0, 0, 0)
    
    
    
    def closest_color(rgb):
        # Vérifier s'il y a une correspondance exacte dans EN_bck_color
        exact_match = [key for key, value in EN_bck_color.items() if value == rgb]
        if exact_match:
            return exact_match[0]
        tree = cKDTree(list(colors.values()))
        _, index = tree.query(rgb)
        return list(colors.keys())[index]

    # Vérifier si le style est au format rgb()
    if style.startswith(("rgb(", "rgba(")):
        rgb_value = style.split("(")[1].split(")")[0]
        try:
            rgb_components = rgb_to_tuple(rgb_value)
            if all(component < 50 for component in rgb_components):
                return default_color
            else:
                return closest_color(rgb_components)
        except ValueError:
            return default_color
    # Vérifier si le style est au format hexadécimal
    elif style.startswith("#") and (len(style) == 7 or len(style) == 4):
        if len(style) == 4:
            style = "#" + style[1] * 2 + style[2] * 2 + style[3] * 2           
        hex_value = style.lstrip("#")
        rgb_components = tuple(int(hex_value[i:i+2], 16) for i in (0, 2, 4))
        if all(component < 50 for component in rgb_components):
            return default_color
        else:
            return closest_color(rgb_components)
    elif style in ('red', 'blue', 'green', 'black'):
        return style
    else:
        return default_color  # Format non reconnu, rouge mis par défaut


# Fonction pour extraire le texte de niveau supérieur sans garder le texte des sous-éléments
def extract_top_level_text(element):
    """Extract top level text without text inside childen block tags

    Returns:
        str: text
    """
    result = []
    for item in element.contents:
        if isinstance(item, str): # sans balise
            result.append(item)
            log=f"-------- chaine simple, on ajoute {item}"
        elif item.name in liste_balisesBlock:
            log=f"-------- {item.name} est une balise bloc, on passe"
            break
        elif item.name=='br':# cas des br pour les sauts de ligne
            result.append('\n')
            log = f"-------- ajout saut de ligne"
        else: # balise non "bloc", on boucle
            log = f"-------- autre cas (balise inline?), on boucle"
            result.append(extract_top_level_text(item))
        #log_debug(f"--- extraction top level, item {item} ==> {log}", logging.NOTSET)
    return ''.join(result)


def get_files(xml_content: ET.Element, dest_folder):
    """Génère les fichiers en enregistrant leurs infos dans un dictionnaire

    Args:
        xml_file (str): Chemin du fichier xml à traiter
        dest_folder (str): Chemin pour l'enregistrement des fichiers

    Returns:
        dic: dictionnaire avec ('hash du fichier': ('id du fichier','nom du fichier', 'type mime du fichier', taille, 'type AT'))
    """
    files_info_dict = {} 
    log_debug(f"-- Get files...", logging.DEBUG)
    
    for resource in xml_content.findall('.//resource'):
        # Récupérer les éléments de la ressource
        data_elem = resource.find("./data")
        mime_elem = resource.find("./mime")
        attributes_elem = resource.find("./resource-attributes")

        if data_elem is not None and data_elem.text is not None and attributes_elem is not None and mime_elem is not None and mime_elem.text is not None:
            # Récupérer les valeurs des éléments
            data_base64: str  = data_elem.text.strip()
            mime: str  = mime_elem.text.strip()
            file_type: str  = ""
            file_id :str = "file" + generate_random_id()
            for type, mime_list in models.mime.TYPE.items():
                if mime in mime_list:
                    file_type = type
                    break
                else:
                    file_type = "File"

            elt_file_name = attributes_elem.find("./file-name")
            if elt_file_name is not None and elt_file_name.text is not None:
                original_filename: str = attributes_elem.find("./file-name").text.strip()
            else:
                original_filename = "noname_" + data_base64[:6]
            sanitized_filename = sanitize_filename(original_filename)
            try:
                data_decode = base64.b64decode(data_base64)
            except base64.binascii.Error as e:
                log_debug(f"--- Error during base64 decoding : {e}", logging.DEBUG)
                continue
            
            # Calculer le hash (MD5) du contenu
            hash_md5 = hashlib.md5(data_decode).hexdigest()

            #Avoid crushes multiples files with same name
            unique_sanitized_filename = hash_md5 + sanitized_filename
            destination_path = os.path.join(dest_folder, unique_sanitized_filename)
            if not os.path.exists(dest_folder):
                os.makedirs(dest_folder)
            with open(destination_path, 'wb') as outfile:
                outfile.write(data_decode)
            file_size = os.path.getsize(destination_path) * 8

            files_info_dict[hash_md5] = FileInfo(file_id, unique_sanitized_filename, original_filename, mime, file_size, file_type, hash_md5)    
        else:
            log_debug(f"--- Resource with empty element for one note", logging.DEBUG)

    return files_info_dict


def download_image(href, dest_folder):
    """Download image from a href link

    Args:
        href: img link
        dest_folder (_type_): _description_

    Returns:
        False, String message d'erreur
        True, FileInfo(file_id, unique_sanitized_filename, original_filename, mime_type=None, file_size=file_size, file_type="Image")
    """
    # Fonction de téléchargement de l'image
                # Elle doit 
                    # déposer le fichier dans le sous-dossier files en ajoutant une chaine aléatoire devant (généré via generate_random_id())
                    # retourner un état OK, le nom du fichier avec la chaine aléatoire (files\xxxxxxxxxmonfichier.jpg), le hash du fichier (hash_md5 = hashlib.md5(monfichier).hexdigest()), le nom du fichier d'origine avec l'extension et l'extension seule)
                # Et si une erreur est rencontrée, retourner un état NOK et un message
                    # Message = "Image non trouvée = " + lien si c'est une erreur web ou un autre message plus générique le cas échéant
                    
    try:
        # Télécharger l'image à partir du lien href
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        req = urllib.request.Request(href,headers={'User-Agent': user_agent})
        response = urllib.request.urlopen(req)
        if response.status == 200:
            # Lire le contenu de la réponse
            image_data = response.read()
            
            # Calculer le hash MD5 de l'image téléchargée
            hash_md5 = hashlib.md5(image_data).hexdigest()
            
            # Générer un nom de fichier aléatoire pour éviter les conflits
            random_id = generate_random_id()
            original_filename=os.path.basename(urlparse(href).path)
            filename = f"{hash_md5}_{original_filename}"
            
            #id du fichier json
            file_id :str = "file" + generate_random_id()
            
            # Obtenir le nom du fichier original et son extension
            file_extension = os.path.splitext(filename)[1]
            sanitized_filename = sanitize_filename(original_filename)
            unique_sanitized_filename = hash_md5 + sanitized_filename
            
            # Enregistrer l'image dans le dossier de destination
            files_path = os.path.join(dest_folder, "files")
            full_path = os.path.join(files_path, unique_sanitized_filename)
            if not os.path.exists(files_path):
                os.makedirs(files_path)
            with open(full_path, 'wb') as outfile:
                outfile.write(image_data)
            file_size = os.path.getsize(full_path) * 8

            
            files_info_dict = FileInfo(file_id, unique_sanitized_filename, original_filename, mime_type=None, file_size=file_size, file_type="Image", hash_md5=hash_md5)    
            
            
            # Retourner les informations sur l'image téléchargée
            return True, files_info_dict
        else:
            # Retourner un état d'erreur si le téléchargement a échoué
            return False, f"Failed to download image from {href}"
    except Exception as e:
        # Retourner un état d'erreur si une exception est levée lors du téléchargement
        return False, f"An error occurred while downloading image from {href}: {e}"

    

def process_details_to_json(content: ET.Element, page_model: Model.Page, working_folder: str):
    """ Retrieves note details """
    log_debug(f"- Retrieving note details", logging.DEBUG)
    title_element = content.find("title")
    title = title_element.text if title_element is not None else "Default Title"   
    log_debug(f"- Note title : {title}", logging.NOTSET)
    page_model.edit_details_key("name", title)
    
    # created date
    created_date_element = content.find("created")
    if created_date_element is not None and created_date_element.text is not None:
        created_date : str = created_date_element.text
        date_format = "%Y%m%dT%H%M%SZ"
        date_object = datetime.strptime(created_date, date_format)
        created_timestamp = int(date_object.timestamp())
        page_model.edit_details_key("createdDate", created_timestamp)
    
    # Modified date as converted date
    timestamp_converted = int(time.time())
    page_model.edit_details_key("lastModifiedDate", timestamp_converted)

    # tags
    tags_list = content.findall("tag")
    tags_id_lists=[]
    for element in tags_list:
        if element.text is not None:
            log_debug(f"Ajout de l'item tag '{element.text}'",logging.NOTSET)
            # On génère une clé, unique pour chaque tag pour éviter les doublons
            try:
                tag_key = (hashlib.md5(element.text.encode()).hexdigest())[:24]
                tag_id = "id_"+tag_key
            except Exception as e:
                log_debug(f"Error during tag processing : {e}", logging.ERROR)
                continue
            # On ne traite que si ce tag  n'est pas traité (donc fichier inexistant)
            filename = f"tagItem_{sanitize_filename(tag_key)}.json"
            filepath = os.path.join(working_folder, filename)
            if not os.path.exists(filepath):
                # On génère le modèle
                tag_option: Model.Tag_Option = Model.Tag_Option()
                # On renseigne key et uniqueKey ("opt-" + key), et l'id
                tag_option.edit_name(element.text)
                tag_option.edit_key(tag_key)
                tag_option.edit_id(tag_id)
                # On met une couleur défini suivant la clé (pour avoir des couleurs différentes mais fixe pour un même tag)
                colors = {"grey", "yellow", "orange", "red", "pink", "purple", "blue", "ice", "teal", "lime"}
                hex_value = int(tag_key, 16)
                index = hex_value % len(colors)
                selected_color = list(colors)[index]
                tag_option.edit_color(selected_color)
                # On génère le fichier à partir de Key (donc unique pour chaque chaine)
                with open(filepath, 'w', encoding='utf-8') as file:
                    json.dump(tag_option.to_json(), file, indent=2)
            else:
                log_debug(f"File '{filename} already exist'",logging.NOTSET)
            # Ajout au json de la note
            tags_id_lists.append(tag_id)
        else:
            log_debug(f"Empty text for tag", logging.WARNING)
    if tags_id_lists:
        page_model.edit_details_key("202312evernotetags",tags_id_lists)

def extract_text_from_codeblock(content):
    """Extrait le texte d'un codeblock : ajout de saut de ligne si besoin et nettoyage des balises

    Args:
        content (_type_): contenu html

    Returns:
        _type_: Chaine texte nettoyé
    """
    extracted_text = ""

    for div_child in content.children:
        # Traitement d'un bloc
        if div_child.name:
            # S'il a un un tag, on l'envoi en récursif pour le détailler
            extracted_text += extract_text_from_codeblock(div_child) 
            div_text=extract_top_level_text(div_child)
            # Si c'est une balise type bloc avec du texte, on ajoute un saut de ligne à la fin
            if div_text and div_child.name in liste_balisesBlock:
                    extracted_text += "\n" 
            
        # Plus de balise, juste du texte : on l'ajoute
        else:
            extracted_text += div_child.string.rstrip()
            
    return extracted_text

def process_codeblock(content, div_id, page_model: Model.Page):
    """Process code block with all content including children tags
    
    Args:
        content (_type_): _description_
        page_model (Model.Page): _description_
    """
    extracted_text = extract_text_from_codeblock(content)
            
    # Estimation du type de langage
    text_language = None
    max_occurrences = 0
    for lang, patterns in language_patterns.items():
        occurrences = 0
        for pattern in patterns:
            occurrences += len(re.findall(pattern, extracted_text))
        
        # Mise à jour de text_language si le nombre d'occurrences est plus élevé
        if occurrences > max_occurrences:
            max_occurrences = occurrences
            text_language = lang
            
    shifting_left = extract_shifting_left(content)
    page_model.add_block(div_id, shifting=shifting_left, text = extracted_text)
    page_model.edit_text_key(div_id, "style", "Code")
    page_model.edit_block_key(div_id,"lang",text_language, master_key="fields")
    
    pass


def process_table(table_content, page_model: Model.Page):
    # Crée un ID pour la table, la liste des colonnes et la liste des lignes et créé les blocs
    table_id = generate_random_id()
    page_model.add_block(table_id, shifting=extract_shifting_left(table_content))
    page_model.edit_block_key(table_id, "table", {})
    
    columns_list_id = generate_random_id()
    page_model.add_block(columns_list_id)
    page_model.edit_block_key(columns_list_id, "style", "TableColumns", master_key="layout")
    page_model.add_children_id(table_id,columns_list_id)
    
    rows_list_id = generate_random_id()
    page_model.add_block(rows_list_id)
    page_model.edit_block_key(rows_list_id, "style", "TableRows", master_key="layout")
    page_model.add_children_id(table_id,rows_list_id)

    # Variables pour stocker les IDs des colonnes et des lignes
    columns_ids = []
    rows_ids = []

    try:
        # Récupération des colonnes
        colgroup = table_content.find('colgroup')
        if colgroup:
            cols = colgroup.find_all('col')
            for col in cols:
                col_id = generate_random_id()
                columns_ids.append(col_id)
                page_model.add_children_id(columns_list_id,col_id)
                # Récupération largeur
                col_style = col.get('style')
                styles = extract_styles(col_style) if col_style else {}
                col_width_px = styles.get("width", None)
                col_width = int(col_width_px.replace("px", "")) if col_width_px else None
                # Crée un bloc pour chaque colonne
                page_model.add_block(col_id, shifting=None, width=col_width)
                page_model.edit_block_key(col_id,"tableColumn",{})

        # Récupération des lignes
        rows = table_content.find_all('tr')
        
        # il faut gérer les rowspan, on va stocker temporairement à chaque ligne les "futures" valeurs de la ligne suivantes
        cells_for_future_row: List[str] = []
        TEMP_nb_row = 0
        for row in rows:
            row_id = generate_random_id()
            rows_ids.append(row_id)
            # Ajout ligne dans la liste des lignes
            page_model.add_children_id(rows_list_id, row_id)
            # Création bloc ligne
            page_model.add_block(row_id, shifting=None)
            page_model.edit_block_key(row_id,"tableRow",{})
            
            # On doit traiter la liste des colonnes pour cette nouvelle ligne
            columns_ids_todo = columns_ids.copy()
            
            # Si on a des cellules récupérés (rowspan), on les gère
            if cells_for_future_row:
                for id in cells_for_future_row:
                    cell_id = f"{row_id}-{id}"
                    page_model.add_block(cell_id, shifting=None, text="")
                    page_model.add_children_id(row_id,cell_id)
                    # puis on retire cette colonne de la liste des colonnes à traiter
                    columns_ids_todo.remove(id)
                cells_for_future_row.clear()

            tds = row.find_all('td')
            # Puis parcours des cellules de la ligne
            index_columns = 0
            for td in tds:
                col = columns_ids_todo[index_columns]
                cell_id = f"{row_id}-{col}"
                # Ajout de la cellule dans le bloc de la ligne
                page_model.add_children_id(row_id,cell_id)
                page_model.add_block(cell_id, shifting=None, text="")
                
                # Traitement contenu de la cellule
                # TODO : voir pour mutualiser avec process_div_children()
                # Il faudrait aussi "mutualiser" les div ou autres : dans AT, il n'y a qu'un bloc
                # Impact sur la longueur du texte, sur ce que récupérer (style sur div, etc)
                # Pour l'instant, on récupère le style de la 1ère div
                first_child = td.find('div')
                style = extract_styles(first_child.get('style'))
                if 'text-align' in style:
                    if style['text-align'] == 'center':
                        page_model.edit_block_key(cell_id,"align","AlignCenter")
                    elif style['text-align'] == 'right':
                        page_model.edit_block_key(cell_id,"align","AlignRight")
                        
                # Puis on supprime les notions de div et on traite l'ensemble 
                cleaned_td = td
                for tag in cleaned_td.find_all(['div', 'ol', 'ul', 'li']):
                    if tag.name == "li":
                        tag.insert_before('\n')
                    tag.unwrap()
                for br in td.find_all('br'):
                    br.replace_with('\n')
                extract_text_with_formatting(cleaned_td, cell_id, page_model)
                
                # récupération style sur td
                styles = extract_styles(td.get('style'))
                
                # Récup des infos
                if "vertical-align" in styles and styles["vertical-align"] == "middle":
                    page_model.edit_block_key(cell_id,"verticalAlign","VerticalAlignMiddle")
                if "color" in styles:
                    param = extract_color_from_style(styles["color"])
                    page_model.edit_text_key(cell_id,"color",param)
                if 'background-color' in styles:
                    param = extract_color_from_style(styles["background-color"])
                    page_model.edit_block_key(cell_id,"backgroundColor",param)

                # Gère les cellules fusionnées
                colspan = int(td.get('colspan', 1))
                if colspan > 1:
                    for _ in range(colspan - 1):
                        index_columns+=1
                        next_col = columns_ids_todo[index_columns]  # Avance dans les colonnes
                        cell_id = f"{row_id}-{next_col}"
                        page_model.add_block(cell_id, shifting=None, text="")
                        page_model.add_children_id(row_id,cell_id)
                
                rowspan = int(td.get('rowspan', 1))
                if rowspan > 1:
                    for _ in range(rowspan - 1):
                        cells_for_future_row.append(col)
                        
                index_columns+=1
        
            TEMP_nb_row+= 1
    except Exception as e:
        log_debug(e, logging.ERROR)
        log_debug('This table contains multiples rowspan/colspan that is not supported in this version.', logging.ERROR)
        # TODO : cas des rowspans multiples...


def extract_text_with_formatting(div_content, div_id, page_model: Model.Page):
    """Analyze inline tags to note text formatting in the json

    Args:
        div_content (_type_): Tag content
        div_id (_type_): block id for json
        page_model (Model.Page): Page model
    """
    # Définition des balises inline à traiter
    formatting_tags = ['span', 'b', 'u', 'i', 's', 'a','font', 'strong', 'em', 'en-todo']
    div_text = extract_top_level_text(div_content)
    log_debug(f"--- extract text and format ---", logging.NOTSET)
    
    if div_text:
        # Ajout du block text
        log_debug(f"--- Add text to json : {div_text}", logging.DEBUG)
        page_model.add_text_to_block(div_id, text=div_text)
        
        # On récupère les tags avec pour chaque le début et fin
        for element in extract_tag_info(div_content, formatting_tags):
            start = element['start']
            end = element['end']
            tag_object=element['tag_object']
            tag_name = tag_object.name

            formatting_type = None
            param = None

            text_style = tag_object.get('style')
            styles = extract_styles(text_style) if text_style else {}
            log_debug(f"Style extrait : {styles}")
            
            # Récup des infos
            if ("font-weight" in styles and styles["font-weight"] == "bold") or tag_name == 'b' or tag_name == 'strong':
                formatting_type = "Bold"
                log_debug(f"Ajout format {formatting_type if formatting_type else None} de {start} à {end}")
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            if ("text-decoration" in styles and styles["text-decoration"] == "underline") or tag_name == 'u':
                formatting_type = "Underscored"
                log_debug(f"Ajout format {formatting_type if formatting_type else None} de {start} à {end}")
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            if ("font-style" in styles and styles["font-style"] == "italic") or tag_name == 'i' or tag_name == 'em':
                formatting_type = "Italic"
                log_debug(f"Ajout format {formatting_type if formatting_type else None} de {start} à {end}")
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            if ("text-decoration" in styles and styles["text-decoration"] == "line-through") or tag_name == 's':
                formatting_type = "Strikethrough"
                log_debug(f"Ajout format {formatting_type if formatting_type else None} de {start} à {end}")
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            if "color" in styles or tag_object.get('color'):
                formatting_type = "TextColor"
                if "color" in styles:
                    color_value = styles["color"]
                else:
                    color_value = tag_object.get("color")
                param = extract_color_from_style(color_value)
                log_debug(f"Ajout format {formatting_type if formatting_type else None} = {param} de {start} à {end} -- Couleur originelle : {color_value}")
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            if 'background-color' in styles:
                formatting_type = "BackgroundColor"
                param = extract_color_from_style(styles["background-color"])
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            if tag_name == 'a':
                formatting_type = "Link"
                param = tag_object.get('href')
                log_debug(f"Ajout format {formatting_type if formatting_type else None} de {start} à {end}")
                page_model.add_mark_to_text(div_id, start, end, mark_param=param if param else None, mark_type=formatting_type if formatting_type else None)
            # cas de ces checkbox, en balise autofermante devant le texte!
            if tag_name == 'en-todo' and 'checked' in tag_object.attrs: 
                page_model.edit_text_key(div_id,"style",'Checkbox')
                if 'true' in tag_object['checked']:
                    page_model.edit_text_key(div_id,"checked",True)
                else:
                    page_model.edit_text_key(div_id,"checked",False)

def process_content_to_json(content: str, page_model, note_id, files_dict, working_folder :str):
    """Find <en-note> tag from content to create the parent element and calling a function for child elements

    Args:
        content (str): content of <content< from source file
        page_model (_type_): Page object generated for this note 
        note_id (_type_): root id racine (used to link files, blocs, ... )
        files_dict (_type_): Dictionnary of available files 
    """
    log_debug(f"- Converting content...", logging.DEBUG)
    # Converting to soup for specific html parsing
    soup = BeautifulSoup(content, 'html.parser')  # Utilisation de l'analyseur HTML par défaut

    # Créer l'élément JSON pour la première div (shifting = -1)
    root_block = soup.find('en-note')
    if root_block:
        block_id = note_id
        page_model.add_block(block_id,-1)
        
        
        # cas de texte avant les 1ères balises
        div_text = extract_top_level_text(root_block)
        if div_text:
            log_debug(f"Text out of html block tag {div_text}", logging.NOTSET)
            div_id = generate_random_id()
            page_model.add_block(div_id,shifting=0)
            extract_text_with_formatting(root_block, div_id, page_model)
            
        process_div_children(root_block, page_model, note_id, files_dict, working_folder)
        
    else:
        log_debug(f"'en-note' element not find", logging.ERROR)


def process_div_children(div, page_model: Model.Page, note_id, files_dict, working_folder :str):
    """Process all child tag

    Args:
        div (_type_): _description_
        page_model (Model.Page): _description_
        files_dict (_type_): _description_
        table (bool, optional): Indicate if it's a loop for a table or the default treatment. Defaults to False.
    """
    log_debug(f"- Converting childrens...", logging.DEBUG)
    children = div.find_all(liste_balisesBlock)
    cell_id=None
    for child in children:
        log_debug(f"-------------------------", logging.NOTSET)
        # élément d'une table, on passe car tous les éléments sont à traiter dans la table (div, media, ...)
        if not cell_id and child.find_parent('td'):
            continue
        # Traitement d'une cellule d'une table, l'ID est déjà défini
        if cell_id:
            div_id = cell_id
        else:
            div_id = generate_random_id()
        shifting_left = extract_shifting_left(child)
        
        div_text = extract_top_level_text(child)

        # On commence par les blocs sans texte
        if child.name == 'hr':
            page_model.add_block(div_id, shifting=shifting_left)
            page_model.edit_block_key(div_id, "div",{})         
        # Traitement des fichiers à intégrer
        elif child.name == 'en-media':
            hash = child.get('hash')
            if hash in files_dict:
                file_info :FileInfo = files_dict[hash]
                file_id = file_info.file_id
                original_filename = file_info.original_filename
                mime = file_info.mime_type
                file_size = file_info.file_size
                file_type = file_info.file_type
                # Redimensionné? Il faut retourner width="340px" divisé par style="--en-naturalWidth:1280" ; ex : style="--en-naturalWidth:1280; --en-naturalHeight:512;" width="340px" />
                text_style = child.get('style')
                styles = extract_styles(text_style) if text_style else {}
                
                embed_width = child.get('width') # Redimension dans Evernote
                original_width = styles.get("--en-naturalWidth") # Dimension naturelle de l'objet
                relative_width = None  # Ratio de la version embed, la seule valeur utilisée dans Anytype
                # On calcul, en mettant la valeur par défaut pour toutes les erreurs potentielles
                try:
                    embed_width_value = float(embed_width.replace("px", ""))
                    original_width_value = float(original_width)
                    embed_width_int = int(embed_width_value)
                    original_width_int = int(original_width_value)
                    if embed_width_int != 0:
                        relative_width = embed_width_int / original_width_int
                except Exception :
                    relative_width = None
                
                
                # Format lien? 
                style_attr = child.get('style')
                format = 'link' if style_attr and '--en-viewAs:attachment;' in style_attr else None
                page_model.add_block(div_id, shifting=shifting_left, width = relative_width)
                page_model.add_file_to_block(div_id, file_id = file_id, hash = hash, name = original_filename, file_type = file_type, mime = mime, size = file_size, format=format )
        
        # img avec source web
        elif child.name == 'img':
            # Exemple : <img style="--en-naturalWidth:1440; --en-naturalHeight:810;" height="476px" width="701px" src="https://www.exemple.com/truc/mon-image.jpg" />
            # Récupération du lien
            href = child.get('src')
            log_debug(f"image à télécharger : {href}", logging.NOTSET)
            download_state, download_content = download_image(href,working_folder)
            log_debug(f"{download_content}", logging.NOTSET)
            
            if download_state:
                # Enregistrement du bloc
                # Gestion redimensionnement
                text_style = child.get('style')
                styles = extract_styles(text_style) if text_style else {}
                embed_width = child.get('width') # Redimension dans Evernote
                original_width = styles.get("--en-naturalWidth") # Dimension naturelle de l'objet
                relative_width = None  # Ratio de la version embed, la seule valeur utilisée dans Anytype
                # On calcul, en mettant la valeur par défaut pour toutes les erreurs potentielles
                try:
                    embed_width_value = float(embed_width.replace("px", ""))
                    original_width_value = float(original_width)
                    embed_width_int = int(embed_width_value)
                    original_width_int = int(original_width_value)
                    if embed_width_int != 0:
                        relative_width = embed_width_int / original_width_int
                except Exception :
                    relative_width = None
                
                page_model.add_block(div_id, shifting=shifting_left, width = relative_width)
                page_model.add_file_to_block(div_id, file_id = download_content.file_id, hash = download_content.hash_md5, name = download_content.original_filename, 
                                             file_type = download_content.file_type, mime = None, size = download_content.file_size, format="Image" )
                
                # Création du json du fichier
                img_dict={} 
                img_dict[download_content.hash_md5] = download_content
                process_file_to_json(note_id,img_dict,working_folder)
                
                
            else:
                download_content:str
                page_model.add_block(div_id, shifting=shifting_left)
                page_model.add_text_to_block(div_id, text=download_content) 
                log_debug(f"{download_content}", logging.WARNING)

        # Traitement bloc code (div racine sans texte); "-en-codeblock:true" et "--en-codeblock:true" co-existent...
        elif (child.name == 'div' and 'style' in child.attrs and '-en-codeblock:true' in child['style']) or child.name=='pre':
                process_codeblock(child, div_id, page_model)
        #Traitement table
        elif child.name == 'table':
            process_table(child, page_model)
        # Traitement des blocs demandant du contenu texte
        elif div_text:
            # les div enfant des blocs codes doivent être exclues du traitement global
            parent_div = child.find_parent('div')
            parent_pre = child.find_parent('pre')
            if child.name == 'div' and ((parent_div and 'style' in parent_div.attrs and '-en-codeblock:true' in parent_div['style']) or parent_pre):
                pass
            # Traitements spécifiques
            else:
                # Traitement spécifique pour les listes!
                parent_list = child.find_parent(['ol', 'ul'])
                if parent_list:
                    #Est-ce dans une liste imbriquée? 1ère étape pouvoir pouvoir placer le childrenIds!
                    # TODO : ajout imbrication à l'imbrication existante? Si padding = 40 et imbrication 40 : traiter comme 80?
                    #        A tester quels cas EN peut générer...
                    nested_level = len(parent_list.find_parents(['ol', 'ul']))
                    if nested_level > 0:
                        # On va traiter comme les blocs décalés...
                        shifting_left = 40 * (nested_level)
                
                # Puis on créé le bloc
                page_model.add_block(div_id, shifting=shifting_left)
                
                # Traitement texte
                extract_text_with_formatting(child, div_id, page_model)
                
                # Traitements styles du bloc
                style = extract_styles(child.get('style'))
                if 'text-align' in style:
                    log_debug(f"Extraction style text_align : {style}",logging.NOTSET)
                    if style['text-align'] == 'center':
                        page_model.edit_block_key(div_id,"align","AlignCenter")
                    elif style['text-align'] == 'right':
                        page_model.edit_block_key(div_id,"align","AlignRight")

                # Et style si c'est une liste
                if parent_list:
                    log_debug(f"C'est une liste", logging.NOTSET)
                    if parent_list.name == 'ol':
                        style_liste = 'Numbered'
                    elif parent_list.name == 'ul' and parent_list.has_attr('style') and '--en-todo:true' in parent_list['style']:
                        style_liste = 'Checkbox'
                        li_parent = child.find_parent('li')
                        if li_parent and li_parent.has_attr('style') and '--en-checked:true' in li_parent['style']:
                            page_model.edit_text_key(div_id,"checked",True)
                    else:
                        style_liste = 'Marked'
                    page_model.edit_text_key(div_id,"style",style_liste)
                
                
                # et style des titres
                parent_title = child.find_parent(['h1', 'h2', 'h3'])
                if  child.name in ['h1', 'h2', 'h3']:
                    page_model.edit_text_key(div_id,"style","Header" + child.name[1:])
                elif parent_title:
                    page_model.edit_text_key(div_id,"style","Header" + parent_title.name[1:])
                
                # Autres niveaux de titres, inexistants dans Anytype : on met en gras
                if  child.name in ['h4', 'h5', 'h6'] :
                    text_lenght = len(div_text)
                    page_model.add_mark_to_text(div_id, 0, text_lenght, mark_type="Bold")


def process_file_to_json(page_id :str, files_dict :dict[str, FileInfo], working_folder :str):
    # On génère le modèle json
    for hash_md5, file_info in files_dict.items():
        # On génère le modèle json pour chaque fichier
        file_json: Model.File_Object = Model.File_Object()
        file_json.edit_id(file_info.file_id)
        file_json.edit_backlinks(page_id)
        # Génération du nom : ici il faut retirer l'extension
        filename_without_extension = os.path.splitext(file_info.original_filename)[0]
        file_json.edit_name(filename_without_extension)
        file_with_path = "files/"+file_info.unique_filename
        file_json.edit_source(file_with_path)
        # Chemin complet pour enregistrer le fichier JSON
        filepath = os.path.join(working_folder, f"{file_info.file_id}.json")
        # On génère le fichier à partir de Key (donc unique pour chaque chaîne)
        with open(filepath, 'w', encoding='utf-8') as file:
            json.dump(file_json.to_json(), file, indent=2)
    
    

def convert_files(enex_files_list: list, options: Type[Options]):
    """Convert enex file from the list into json files

    Args:
        enex_files_list (list): list of enex file to convert

    Returns:
        string: number of notes converted
    """
    
    log_debug(f"-----CONVERTING-----", logging.DEBUG)
    if not enex_files_list:
        log_debug("No file to convert.", logging.INFO)
        return
    
    source_folder = os.path.dirname(enex_files_list[0])
    if options.zip_result:
        working_folder = os.path.join(source_folder, "Working_folder")
    else:
        working_folder = os.path.join(source_folder, "Converted_files")
    os.makedirs(working_folder, exist_ok=True)
    files_dest_folder = os.path.join(working_folder, "files")
    
    # Add Relation "Evernote tag"
    dirname = os.path.dirname(__file__)
    relation_file = os.path.join(dirname, "models/Evernote_Tag_Relation.json")
    shutil.copy(relation_file,working_folder)
    
    nb_notes = 0
    for enex_file in enex_files_list:
        log_debug(f"Converting {os.path.basename(enex_file)}...", logging.INFO)
        with open(enex_file, 'r', encoding='utf-8') as xhtml_file:
            file_content = xhtml_file.read()
            if not file_content:
                log_debug(f"No content in file", logging.ERROR)
                return
        
        try:
            root = ET.fromstring(file_content)
        except ET.ParseError as e:
            log_debug(f"XML parsing error : {e}", logging.ERROR)
            continue
        except Exception as e:
            log_debug(f"XML treatment error : {e}", logging.ERROR)
            continue
        
        # Parcours des notes dans le fichier
        for note_xml in root.iter("note"):
            log_debug(f"Treatment note {nb_notes}...", logging.INFO)
            
            # Attribution de l'ID de la page
            note_id :str = generate_random_id()
            
            # Traitement des fichiers (base64 vers fichiers) avant le reste car référencé dans le parcours des notes
            files_dict = get_files(note_xml, files_dest_folder)
            log_debug("Contenu de files_dict :", logging.NOTSET)
            [log_debug(f"{hash_md5}: (file_id={file_info.file_id}, file_name={file_info.unique_filename}, original file_name={file_info.original_filename}, mime_type={file_info.mime_type}, file_size={file_info.file_size}, file_type={file_info.file_type})", logging.NOTSET) for hash_md5, file_info in files_dict.items()]

            # Génération des JSON fichiers
            process_file_to_json(note_id,files_dict, working_folder)
            
            # Utilisation de la classe Model.Page pour créer le JSON
            page_model: Model.Page = Model.Page()

            # Extraction du contenu de la balise <content> et traitement
            content_element = note_xml.find('content')
            if content_element is None or content_element.text is None:
                log_debug(f"Note {nb_notes} has no content!", logging.DEBUG)
                continue
            
            content: str = content_element.text
            process_content_to_json(content, page_model, note_id, files_dict, working_folder)
            
            # Processing xml tags (other than <content>)
            process_details_to_json(note_xml, page_model, working_folder)

            # Nettoyer les clés "shifting" si nécessaire
            page_model.cleanup()
            
            note_title = page_model.page_json["snapshot"]["data"]["details"]["name"]
            # Filename with the create date, in case several notes have the same title
            creation_date: str = page_model.get_creation_date()
            filename = f"{sanitize_filename(note_title)}_{creation_date}.json"
            with open(os.path.join(working_folder, filename), 'w', encoding='utf-8') as file:
                json.dump(page_model.to_json(), file, indent=2)
            nb_notes += 1
    
    # On zip le résultat
    if options.zip_result:
        log_debug(f"Create zip file", logging.DEBUG)
        current_time = datetime.now()
        zip_name = current_time.strftime("ConvertedFiles_%d%m%Y_%H%M%S")
        zip_path = os.path.join(source_folder, zip_name)
        shutil.make_archive(zip_path, 'zip', working_folder)
        shutil.rmtree(working_folder)

    log_debug(f"Conversion completed: {nb_notes} notes converted", logging.INFO)
    return nb_notes
            

def main(version):
    enex_files =[]
    my_options.is_debug=False
    my_options.zip_result=True
    
    parser = argparse.ArgumentParser(description="Convert ENEX files.")
    parser.add_argument("--enex_sources", nargs="+", help="List of ENEX files to convert")
    parser.add_argument("--nozip", action="store_true", default=False, help="Desactivate creation of a zip file")
    parser.add_argument("--debug", action="store_true", default=False, help="Create a debug file")
    parser.add_argument("--test", action="store_true", default=False, help="test with a defaut file")
    args = parser.parse_args()
    
    if args.enex_sources:
        for source in args.enex_sources:
            if os.path.isdir(source):
                if os.path.exists(source):
                    enex_files_from_folder = [os.path.join(source, f) for f in os.listdir(source) if f.endswith('.enex')]
                    enex_files.extend(enex_files_from_folder)
                else:
                    log_debug(f"Error: Directory {source} does not exist.", logging.WARNING)
            elif os.path.isfile(source):
                if source.endswith('.enex'):
                    if os.path.exists(source):
                        enex_files.append(source)
                    else:
                        log_debug(f"Error: File {source} does not exist.", logging.WARNING)
                else:
                    log_debug(f"Warning: {source} is not an ENEX file.", logging.WARNING)
            else:
                log_debug(f"Error: {source} is not a folder nor a file?", logging.ERROR)
    elif args.test:
        # Default value for dev ;-)
        enex_directory = 'Tests/Temp/'
        enex_files = [os.path.join(enex_directory, f) for f in os.listdir(enex_directory) if f.endswith('Bug OSI.enex')]
        
        
    # my_options.tag = "Valeur pour le tag"
    my_options.is_debug = args.debug # Faux par défaut
    my_options.zip_result = not args.nozip # Vrai par défaut
        
    log_debug(f"Launched with CLI {version}, ZIP = {my_options.zip_result}, DEBUG = {my_options.is_debug}", logging.DEBUG)
    # Liste des fichiers enex dans le répertoire
    if enex_files:
        convert_files(enex_files, my_options)
        
    else:
        log_debug(f"No enex file to convert, check if the 'enex_sources' parameter is correct.", logging.ERROR)
    

if __name__ == "__main__":
    main("")