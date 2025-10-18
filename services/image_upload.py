import requests
import os
from typing import Optional


def upload_image_to_imgbb(file_path: str) -> Optional[str]:
    """
    Загружает изображение на imgbb.com и возвращает публичную ссылку
    
    Args:
        file_path: Путь к локальному файлу изображения
    
    Returns:
        Публичная ссылка на изображение или None при ошибке
    """
    try:
        # Получаем API ключ imgbb (можно получить бесплатно на imgbb.com)
        imgbb_api_key = os.getenv("IMGBB_API_KEY")
        if not imgbb_api_key:
            print("IMGBB_API_KEY не найден в переменных окружения")
            return None
        
        # Загружаем файл
        with open(file_path, 'rb') as file:
            files = {'image': file}
            data = {'key': imgbb_api_key}
            
            response = requests.post('https://api.imgbb.com/1/upload', files=files, data=data)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return result['data']['url']
                else:
                    print(f"Ошибка imgbb: {result.get('error', {}).get('message', 'Неизвестная ошибка')}")
                    return None
            else:
                print(f"Ошибка HTTP {response.status_code}: {response.text}")
                return None
                
    except Exception as e:
        print(f"Ошибка при загрузке изображения: {str(e)}")
        return None


def upload_image_to_imgur(file_path: str) -> Optional[str]:
    """
    Альтернативный способ загрузки через imgur.com
    
    Args:
        file_path: Путь к локальному файлу изображения
    
    Returns:
        Публичная ссылка на изображение или None при ошибке
    """
    try:
        # Получаем Client ID для imgur (можно получить бесплатно на imgur.com)
        imgur_client_id = os.getenv("IMGUR_CLIENT_ID")
        if not imgur_client_id:
            print("IMGUR_CLIENT_ID не найден в переменных окружения")
            return None
        
        # Загружаем файл
        with open(file_path, 'rb') as file:
            headers = {'Authorization': f'Client-ID {imgur_client_id}'}
            files = {'image': file}
            
            response = requests.post('https://api.imgur.com/3/image', headers=headers, files=files)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return result['data']['link']
                else:
                    print(f"Ошибка imgur: {result.get('data', {}).get('error', 'Неизвестная ошибка')}")
                    return None
            else:
                print(f"Ошибка HTTP {response.status_code}: {response.text}")
                return None
                
    except Exception as e:
        print(f"Ошибка при загрузке изображения: {str(e)}")
        return None


def upload_image(file_path: str) -> Optional[str]:
    """
    Пытается загрузить изображение на различные сервисы
    
    Args:
        file_path: Путь к локальному файлу изображения
    
    Returns:
        Публичная ссылка на изображение или None при ошибке
    """
    # Сначала пробуем imgbb
    url = upload_image_to_imgbb(file_path)
    if url:
        return url
    
    # Если не получилось, пробуем imgur
    url = upload_image_to_imgur(file_path)
    if url:
        return url
    
    print("Не удалось загрузить изображение ни на один сервис")
    return None
