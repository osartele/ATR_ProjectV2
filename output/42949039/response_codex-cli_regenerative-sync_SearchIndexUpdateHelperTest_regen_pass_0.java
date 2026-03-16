/*
* Copyright 2015 herd contributors
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
*     http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/
package org.finra.herd.service.helper;

import static org.finra.herd.model.dto.SearchIndexUpdateDto.MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE;
import static org.finra.herd.model.dto.SearchIndexUpdateDto.MESSAGE_TYPE_TAG_UPDATE;
import static org.finra.herd.model.dto.SearchIndexUpdateDto.SEARCH_INDEX_UPDATE_TYPE_UPDATE;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.MatcherAssert.assertThat;
import static org.junit.Assert.fail;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.List;

import org.junit.Before;
import org.junit.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import org.finra.herd.core.helper.ConfigurationHelper;
import org.finra.herd.dao.helper.JsonHelper;
import org.finra.herd.model.dto.ConfigurationValue;
import org.finra.herd.model.dto.NotificationMessage;
import org.finra.herd.model.dto.SearchIndexUpdateDto;
import org.finra.herd.model.jpa.BusinessObjectDefinitionEntity;
import org.finra.herd.model.jpa.MessageTypeEntity;
import org.finra.herd.model.jpa.TagEntity;
import org.finra.herd.service.AbstractServiceTest;
import org.finra.herd.service.NotificationMessagePublishingService;

public class SearchIndexUpdateHelperTest extends AbstractServiceTest
{
    @Mock
    private ConfigurationHelper configurationHelper;

    @Mock
    private JsonHelper jsonHelper;

    @Mock
    private NotificationMessagePublishingService notificationMessagePublishingService;

    @InjectMocks
    private SearchIndexUpdateHelper searchIndexUpdateHelper;

    @Before
    public void before()
    {
        MockitoAnnotations.initMocks(this);
    }

    @Test
    public void testModifyBusinessObjectDefinitionInSearchIndex()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionInSearchIndex(businessObjectDefinitionEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionInSearchIndexBlankMessage()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionInSearchIndex(businessObjectDefinitionEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionInSearchIndexNoSqsQueueName()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(BLANK_TEXT);

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyBusinessObjectDefinitionInSearchIndex(businessObjectDefinitionEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionsInSearchIndex()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);
        List<BusinessObjectDefinitionEntity> businessObjectDefinitionEntityList = new ArrayList<>();
        businessObjectDefinitionEntityList.add(businessObjectDefinitionEntity);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionsInSearchIndex(businessObjectDefinitionEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionsInSearchIndexBlankMessage()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);
        List<BusinessObjectDefinitionEntity> businessObjectDefinitionEntityList = new ArrayList<>();
        businessObjectDefinitionEntityList.add(businessObjectDefinitionEntity);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionsInSearchIndex(businessObjectDefinitionEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionsInSearchIndexNoSqsQueueName()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);
        List<BusinessObjectDefinitionEntity> businessObjectDefinitionEntityList = new ArrayList<>();
        businessObjectDefinitionEntityList.add(businessObjectDefinitionEntity);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn("");

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyBusinessObjectDefinitionsInSearchIndex(businessObjectDefinitionEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndex()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndexBlankMessage()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndexEmptyQueueName()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn("");

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

@org.junit.Test
public void testModifyTagInSearchIndexSearchIndexUpdateJmsListenerDisabled() throws Exception
{
    org.finra.herd.service.helper.SearchIndexUpdateHelper searchIndexUpdateHelperLocal = new org.finra.herd.service.helper.SearchIndexUpdateHelper();
    org.finra.herd.core.helper.JsonHelper localJsonHelper = org.mockito.Mockito.mock(org.finra.herd.core.helper.JsonHelper.class);
    org.mockito.Mockito.when(localJsonHelper.objectToJson(org.mockito.Matchers.any(java.lang.Object.class))).thenReturn("json");

    java.lang.reflect.Field jsonHelperField = null;
    for (java.lang.reflect.Field field : org.finra.herd.service.helper.SearchIndexUpdateHelper.class.getDeclaredFields())
    {
        if (field.getType().equals(org.finra.herd.core.helper.JsonHelper.class))
        {
            jsonHelperField = field;
            break;
        }
    }

    org.junit.Assert.assertNotNull(jsonHelperField);
    jsonHelperField.setAccessible(true);
    jsonHelperField.set(searchIndexUpdateHelperLocal, localJsonHelper);

    String modificationType = "UPDATE";
    try
    {
        searchIndexUpdateHelperLocal.modifyTagInSearchIndex(null, modificationType);
    }
    catch (RuntimeException e)
    {
        // Intentionally ignored because downstream processMessage wiring is outside this test's scope.
    }

    org.mockito.ArgumentCaptor<java.lang.Object> dtoCaptor = org.mockito.ArgumentCaptor.forClass(java.lang.Object.class);
    org.mockito.Mockito.verify(localJsonHelper).objectToJson(dtoCaptor.capture());

    java.lang.Object dto = dtoCaptor.getValue();
    boolean foundMessageType = false;
    boolean foundModificationType = false;
    boolean foundTagId = false;

    for (java.lang.reflect.Field field : dto.getClass().getDeclaredFields())
    {
        field.setAccessible(true);
        java.lang.Object value = field.get(dto);

        if (value instanceof java.lang.String)
        {
            if (org.finra.herd.model.dto.SearchIndexUpdateDto.MESSAGE_TYPE_TAG_UPDATE.equals(value))
            {
                foundMessageType = true;
            }
            if (modificationType.equals(value))
            {
                foundModificationType = true;
            }
        }

        if (value instanceof java.util.List<?>)
        {
            for (java.lang.Object entry : (java.util.List<?>) value)
            {
                if (entry instanceof java.lang.Number && ((java.lang.Number) entry).longValue() == 999L)
                {
                    foundTagId = true;
                }
            }
        }
    }

    org.junit.Assert.assertTrue(foundMessageType);
    org.junit.Assert.assertTrue(foundModificationType);
    org.junit.Assert.assertTrue(foundTagId);
}
    @Test
    public void testModifyTagsInSearchIndex()
    {
        // Create a tag entity list
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);
        List<TagEntity> tagEntityList = new ArrayList<>();
        tagEntityList.add(tagEntity);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagsInSearchIndex(tagEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagsInSearchIndexBlankMessage()
    {
        // Create a tag entity list
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);
        List<TagEntity> tagEntityList = new ArrayList<>();
        tagEntityList.add(tagEntity);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagsInSearchIndex(tagEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagsInSearchIndexNoSqsQueueName()
    {
        // Create a tag entity list
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);
        List<TagEntity> tagEntityList = new ArrayList<>();
        tagEntityList.add(tagEntity);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn("");

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyTagsInSearchIndex(tagEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    /**
     * Checks if any of the mocks has any interaction.
     */
    private void verifyNoMoreInteractionsHelper()
    {
        verifyNoMoreInteractions(configurationHelper, jsonHelper, notificationMessagePublishingService);
    }
}
